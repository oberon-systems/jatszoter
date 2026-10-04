use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use tokio::task::{JoinHandle, JoinSet};
use tokio::time::timeout;
use tracing::Instrument;
use url::Url;

use crate::libs::agent::{Auth, Runtime};
use crate::libs::api::{Api, Desired, Event, HttpApi, Moved, Signed, Status};
use crate::libs::config::Config;
use crate::libs::error::AgentError;
use crate::libs::images::ImageCache;
use crate::libs::reconcile::{plan, Action};
use crate::libs::vm::{self, Tools, VmSpec};

// Bounds how long a task holds the credential, so a refresh is never held off.
const SIGN_WAIT: Duration = Duration::from_secs(15);
const MAX_REASON: usize = 200;
const LOST: &str = "vm lost";

struct Shared {
    api: HttpApi,
    images: ImageCache,
    root: PathBuf,
    tools: Tools,
}

pub struct QemuRuntime {
    shared: Arc<Shared>,
    capacity: u32,
    tasks: HashMap<String, JoinHandle<()>>,
    fence: Option<JoinHandle<()>>,
}

impl QemuRuntime {
    pub fn new(config: &Config, api: HttpApi) -> Result<Self, AgentError> {
        Ok(Self {
            shared: Arc::new(Shared {
                api,
                images: ImageCache::new(
                    &config.state_dir.join("images"),
                    config.image_limit_bytes,
                )?,
                root: config.state_dir.join("vms"),
                tools: Tools {
                    qemu: config.qemu.clone(),
                    qemu_img: config.qemu_img.clone(),
                },
            }),
            capacity: config.capacity,
            tasks: HashMap::new(),
            fence: None,
        })
    }
}

impl Runtime for QemuRuntime {
    fn reconcile(&mut self, auth: &Auth, desired: Vec<Desired>) {
        if self
            .fence
            .as_ref()
            .is_some_and(|fence| !fence.is_finished())
        {
            tracing::info!("still fencing; skipping the round");
            return;
        }
        self.fence = None;
        self.tasks.retain(|_, task| !task.is_finished());
        let on_disk = match vm::list(&self.shared.root) {
            Ok(on_disk) => on_disk,
            Err(err) => {
                tracing::warn!(error = %err, "listing the VMs failed");
                return;
            }
        };
        let busy: HashSet<String> = self.tasks.keys().cloned().collect();
        for action in plan(&desired, &on_disk, &busy, self.capacity) {
            let deployment_id = action.deployment_id();
            let vm_id = match &action {
                Action::Start(item) | Action::Stop(item) | Action::Lost(item) => item.vm_id.clone(),
                Action::Remove(dir) => on_disk
                    .iter()
                    .find(|vm| &vm.dir == dir)
                    .and_then(|vm| vm.record.as_ref())
                    .map(|record| record.vm_id.clone())
                    .unwrap_or_default(),
            };
            let span = tracing::info_span!("deployment", %deployment_id, %vm_id);
            let work = run(Arc::clone(&self.shared), Arc::clone(auth), action);
            self.tasks
                .insert(deployment_id, tokio::spawn(work.instrument(span)));
        }
    }

    // Returns at once: the fence task waits for the aborted tasks, then destroys every VM.
    fn destroy_all(&mut self) -> Result<(), AgentError> {
        let previous = self.fence.take();
        let aborted: Vec<_> = self
            .tasks
            .drain()
            .map(|(_, task)| {
                task.abort();
                task
            })
            .collect();
        let root = self.shared.root.clone();
        self.fence = Some(tokio::spawn(async move {
            for task in previous.into_iter().chain(aborted) {
                let _ = task.await;
            }
            fence(&root).await;
        }));
        Ok(())
    }
}

async fn fence(root: &Path) {
    let found = match vm::list(root) {
        Ok(found) => found,
        Err(err) => {
            tracing::warn!(error = %err, "listing the VMs to fence failed");
            return;
        }
    };
    let mut destroys = JoinSet::new();
    for vm in found {
        destroys.spawn(async move { (vm::destroy(&vm.dir).await, vm.dir) });
    }
    while let Some(done) = destroys.join_next().await {
        match done {
            Ok((Ok(()), _)) => {}
            Ok((Err(err), dir)) => {
                tracing::warn!(dir = %dir.display(), error = %err, "fencing a VM failed");
            }
            Err(err) => tracing::warn!(error = %err, "a fencing task failed"),
        }
    }
    tracing::info!("fenced");
}

async fn run(shared: Arc<Shared>, auth: Auth, action: Action) {
    match action {
        Action::Start(item) => start(&shared, &auth, &item).await,
        Action::Stop(item) => {
            if let Err(err) = vm::destroy(&shared.root.join(&item.id)).await {
                tracing::warn!(error = %err, "destroying the VM failed");
                return;
            }
            send(
                &shared,
                &auth,
                &item.id,
                Event::Destroy,
                Status::Stopping,
                None,
            )
            .await;
            tracing::info!("VM destroyed");
        }
        Action::Lost(item) => {
            tracing::warn!(status = ?item.status, "the VM is gone");
            send(
                &shared,
                &auth,
                &item.id,
                Event::Fail,
                item.status,
                Some(LOST),
            )
            .await;
        }
        Action::Remove(dir) => match vm::destroy(&dir).await {
            Ok(()) => tracing::info!("stale VM removed"),
            Err(err) => tracing::warn!(error = %err, "removing a stale VM failed"),
        },
    }
}

async fn start(shared: &Shared, auth: &Auth, item: &Desired) {
    let assigned = send(shared, auth, &item.id, Event::Assign, Status::Pending, None).await;
    if !matches!(assigned, Some(Moved::To(_))) {
        return;
    }
    match launch(shared, item).await {
        Ok(()) => tracing::info!("VM started"),
        Err(err) => {
            let reason = reason(&err, &item.image.url);
            tracing::warn!(error = %reason, "starting the VM failed");
            send(
                shared,
                auth,
                &item.id,
                Event::Fail,
                Status::Starting,
                Some(&reason),
            )
            .await;
        }
    }
}

async fn launch(shared: &Shared, item: &Desired) -> Result<(), AgentError> {
    let url = Url::parse(&item.image.url).map_err(|err| AgentError::Image(err.to_string()))?;
    let base = shared
        .images
        .ensure(&url, &item.image.digest)
        .await
        .map_err(|err| match err {
            AgentError::Image(_) => err,
            other => AgentError::Image(other.to_string()),
        })?;
    let spec = VmSpec {
        deployment_id: item.id.clone(),
        vm_id: item.vm_id.clone(),
        cpus: item.resources.cpus,
        memory_mib: resource(item.resources.memory_mib, "memory_mib")?,
        disk_gib: resource(item.resources.disk_gib, "disk_gib")?,
        base,
    };
    vm::create(&shared.root, &spec, &shared.tools)
        .await
        .map(drop)
        .map_err(|err| match err {
            AgentError::Vm(_) => err,
            other => AgentError::Vm(other.to_string()),
        })
}

async fn send(
    shared: &Shared,
    auth: &Auth,
    deployment_id: &str,
    event: Event,
    expected: Status,
    reason: Option<&str>,
) -> Option<Moved> {
    let attempt = async {
        let signer = auth.read().await;
        let Some(signer) = signer.as_ref() else {
            return Ok(None);
        };
        shared
            .api
            .transition(
                &signer.agent_id,
                &signer.credential,
                deployment_id,
                event,
                expected,
                reason,
            )
            .await
            .map(Some)
    };
    match timeout(SIGN_WAIT, attempt).await {
        Ok(Ok(Some(Signed::Accepted(moved)))) => Some(moved),
        Ok(Ok(Some(Signed::Refused))) => {
            tracing::warn!(?event, "the API refused the credential");
            None
        }
        Ok(Ok(None)) => None,
        Ok(Err(err)) => {
            tracing::warn!(?event, error = %err, "transition failed");
            None
        }
        Err(_) => {
            tracing::warn!(?event, "transition timed out");
            None
        }
    }
}

fn resource(value: u64, name: &str) -> Result<u32, AgentError> {
    u32::try_from(value).map_err(|_| AgentError::Vm(format!("{name} {value} is out of range")))
}

// The image URL may carry a token in its query, and a reason reaches the operator.
fn reason(err: &AgentError, url: &str) -> String {
    let mut text = err.to_string();
    if let Some((bare, _)) = url.split_once(['?', '#']) {
        text = text.replace(url, bare);
    }
    text.chars().take(MAX_REASON).collect()
}

#[cfg(test)]
mod tests;
