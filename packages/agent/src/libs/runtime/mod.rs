use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tokio::task::{JoinHandle, JoinSet};
use tokio::time::{sleep, timeout};
use tracing::Instrument;
use url::Url;

use crate::libs::agent::{Auth, Runtime};
use crate::libs::api::{Api, Desired, Event, HttpApi, Moved, Seed, Signed, Status};
use crate::libs::bootstrap::{self, Verdict, MARKER};
use crate::libs::config::Config;
use crate::libs::console;
use crate::libs::error::AgentError;
use crate::libs::images::ImageCache;
use crate::libs::qga::Qga;
use crate::libs::reconcile::{plan, Action};
use crate::libs::vm::{self, OnDisk, Tools, VmSpec};

// Bounds how long a task holds the credential, so a refresh is never held off.
const SIGN_WAIT: Duration = Duration::from_secs(15);
const MAX_REASON: usize = 200;
const LOST: &str = "vm lost";
const PROBE_WAIT: Duration = Duration::from_secs(15);
const QGA_WAIT: Duration = Duration::from_secs(5);
const MARKER_MAX: usize = 256;
const OVERDUE: &str = "bootstrap timeout";
const CONSOLE_RETRY: Duration = Duration::from_secs(5);

type Full = Arc<Mutex<HashSet<String>>>;

struct Shared {
    api: HttpApi,
    api_url: Url,
    images: ImageCache,
    root: PathBuf,
    tools: Tools,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Kind {
    Start,
    Stop,
    Lost,
    Probe,
    Remove,
}

impl Kind {
    fn of(action: &Action) -> Self {
        match action {
            Action::Start(_) => Self::Start,
            Action::Stop(_) => Self::Stop,
            Action::Lost(_) => Self::Lost,
            Action::Probe(_) => Self::Probe,
            Action::Remove(_) => Self::Remove,
        }
    }
}

pub struct QemuRuntime {
    shared: Arc<Shared>,
    capacity: u32,
    tasks: HashMap<String, JoinHandle<()>>,
    kinds: HashMap<String, Kind>,
    consoles: HashMap<String, JoinHandle<()>>,
    full: Full,
    fence: Option<JoinHandle<()>>,
}

impl QemuRuntime {
    pub fn new(config: &Config, api: HttpApi) -> Result<Self, AgentError> {
        Ok(Self {
            shared: Arc::new(Shared {
                api,
                api_url: config.api_url.clone(),
                images: ImageCache::new(
                    &config.state_dir.join("images"),
                    config.image_limit_bytes,
                )?,
                root: config.state_dir.join("vms"),
                tools: Tools {
                    qemu: config.qemu.clone(),
                    qemu_img: config.qemu_img.clone(),
                    xorriso: config.xorriso.clone(),
                    ovmf_code: config.ovmf_code.clone(),
                    ovmf_vars: config.ovmf_vars.clone(),
                },
            }),
            capacity: config.capacity,
            tasks: HashMap::new(),
            kinds: HashMap::new(),
            consoles: HashMap::new(),
            full: Full::default(),
            fence: None,
        })
    }

    fn abort_stopping(&mut self, desired: &[Desired]) -> HashMap<String, JoinHandle<()>> {
        let mut aborted = HashMap::new();
        for item in desired
            .iter()
            .filter(|item| item.status == Status::Stopping)
        {
            if !matches!(self.kinds.get(&item.id), Some(Kind::Start | Kind::Probe)) {
                continue;
            }
            self.kinds.remove(&item.id);
            if let Some(task) = self.tasks.remove(&item.id) {
                tracing::info!(deployment_id = %item.id, "aborting the task of a stopping deployment");
                task.abort();
                aborted.insert(item.id.clone(), task);
            }
        }
        aborted
    }

    // Console tasks live outside `tasks`: they neither make a deployment busy nor take capacity.
    fn keep_consoles(&mut self, auth: &Auth, desired: &[Desired], on_disk: &[OnDisk]) {
        let wanted: Vec<&Desired> = desired
            .iter()
            .filter(|item| {
                matches!(
                    item.status,
                    Status::Starting | Status::Bootstrapping | Status::Ready
                ) && vm::valid_id(&item.id)
                    && on_disk
                        .iter()
                        .any(|vm| vm.alive && vm.dir == self.shared.root.join(&item.id))
            })
            .collect();
        self.consoles.retain(|id, task| {
            if wanted.iter().any(|item| &item.id == id) {
                return !task.is_finished();
            }
            tracing::info!(deployment_id = %id, "aborting the console task");
            task.abort();
            false
        });
        let mut full = self.full.lock().unwrap_or_else(PoisonError::into_inner);
        full.retain(|id| desired.iter().any(|item| &item.id == id));
        for item in wanted {
            if self.consoles.contains_key(&item.id) || full.contains(&item.id) {
                continue;
            }
            let span =
                tracing::info_span!("deployment", deployment_id = %item.id, vm_id = %item.vm_id);
            let work = ship_console(
                Arc::clone(&self.shared),
                Arc::clone(auth),
                item.id.clone(),
                Arc::clone(&self.full),
            );
            self.consoles
                .insert(item.id.clone(), tokio::spawn(work.instrument(span)));
        }
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
        self.kinds.retain(|id, _| self.tasks.contains_key(id));
        let on_disk = match vm::list(&self.shared.root) {
            Ok(on_disk) => on_disk,
            Err(err) => {
                tracing::warn!(error = %err, "listing the VMs failed");
                return;
            }
        };
        self.keep_consoles(auth, &desired, &on_disk);
        let mut aborted = self.abort_stopping(&desired);
        let busy: HashSet<String> = self.tasks.keys().cloned().collect();
        for action in plan(&desired, &on_disk, &busy, self.capacity) {
            let deployment_id = action.deployment_id();
            let vm_id = match &action {
                Action::Start(item)
                | Action::Stop(item)
                | Action::Lost(item)
                | Action::Probe(item) => item.vm_id.clone(),
                Action::Remove(dir) => on_disk
                    .iter()
                    .find(|vm| &vm.dir == dir)
                    .and_then(|vm| vm.record.as_ref())
                    .map(|record| record.vm_id.clone())
                    .unwrap_or_default(),
            };
            let span = tracing::info_span!("deployment", %deployment_id, %vm_id);
            let kind = Kind::of(&action);
            let after = aborted.remove(&deployment_id);
            let work = run(Arc::clone(&self.shared), Arc::clone(auth), action, after);
            self.kinds.insert(deployment_id.clone(), kind);
            self.tasks
                .insert(deployment_id, tokio::spawn(work.instrument(span)));
        }
    }

    // Returns at once: the fence task waits for the aborted tasks, then destroys every VM.
    fn destroy_all(&mut self) -> Result<(), AgentError> {
        let previous = self.fence.take();
        self.kinds.clear();
        let aborted: Vec<_> = self
            .tasks
            .drain()
            .chain(self.consoles.drain())
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

async fn ship_console(shared: Arc<Shared>, auth: Auth, deployment_id: String, full: Full) {
    let dir = shared.root.join(&deployment_id);
    loop {
        match console::ship(&shared.api_url, &auth, &deployment_id, &dir).await {
            Ok(true) => {
                tracing::warn!("the console log is full; shipping stops");
                full.lock()
                    .unwrap_or_else(PoisonError::into_inner)
                    .insert(deployment_id);
                return;
            }
            Ok(false) => tracing::info!("the console socket closed"),
            Err(err) => tracing::warn!(error = %err, "the console socket failed"),
        }
        sleep(CONSOLE_RETRY).await;
    }
}

async fn run(shared: Arc<Shared>, auth: Auth, action: Action, after: Option<JoinHandle<()>>) {
    // A stop waits for the task it aborted, so the destroy never races what that task left.
    if let Some(task) = after {
        let _ = task.await;
    }
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
        Action::Probe(item) => {
            if timeout(PROBE_WAIT, probe(&shared, &auth, &item))
                .await
                .is_err()
            {
                tracing::info!("probing the VM timed out");
            }
        }
        Action::Remove(dir) => match vm::destroy(&dir).await {
            Ok(()) => tracing::info!("stale VM removed"),
            Err(err) => tracing::warn!(error = %err, "removing a stale VM failed"),
        },
    }
}

async fn probe(shared: &Shared, auth: &Auth, item: &Desired) {
    let dir = shared.root.join(&item.id);
    let Some(record) = vm::read_record(&dir) else {
        tracing::warn!("the VM has no readable vm.json");
        return;
    };
    if bootstrap::overdue(record.created_at, item.timeouts.bootstrap_seconds, now()) {
        tracing::warn!("the bootstrap timed out");
        send(
            shared,
            auth,
            &item.id,
            Event::Fail,
            item.status,
            Some(OVERDUE),
        )
        .await;
        return;
    }
    let mut qga = match Qga::connect(&dir.join(vm::QGA), QGA_WAIT).await {
        Ok(qga) => qga,
        Err(err) => {
            tracing::debug!(error = %err, "the guest agent does not answer yet");
            return;
        }
    };
    match item.status {
        Status::Starting => match qga.ping().await {
            Ok(()) => {
                tracing::info!("the guest agent answers");
                send(shared, auth, &item.id, Event::Boot, Status::Starting, None).await;
            }
            Err(err) => tracing::debug!(error = %err, "the guest agent does not answer yet"),
        },
        Status::Bootstrapping => match qga.read_file(MARKER, MARKER_MAX).await {
            Ok(None) => {}
            Ok(Some(marker)) => finish(shared, auth, item, &dir, bootstrap::verdict(&marker)).await,
            Err(err) => tracing::debug!(error = %err, "reading the marker failed"),
        },
        _ => {}
    }
}

async fn finish(shared: &Shared, auth: &Auth, item: &Desired, dir: &Path, verdict: Verdict) {
    match verdict {
        Verdict::Ready => {
            if let Err(err) = vm::eject_seed(dir).await {
                tracing::warn!(error = %err, "ejecting the seed failed");
                return;
            }
            tracing::info!("the VM is bootstrapped");
            send(
                shared,
                auth,
                &item.id,
                Event::Bootstrap,
                Status::Bootstrapping,
                None,
            )
            .await;
        }
        Verdict::Failed(reason) => {
            tracing::warn!(%reason, "the bootstrap failed");
            send(
                shared,
                auth,
                &item.id,
                Event::Fail,
                Status::Bootstrapping,
                Some(&reason),
            )
            .await;
        }
    }
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |elapsed| elapsed.as_secs())
}

async fn start(shared: &Shared, auth: &Auth, item: &Desired) {
    let assigned = send(shared, auth, &item.id, Event::Assign, Status::Pending, None).await;
    if !matches!(assigned, Some(Moved::To(_))) {
        return;
    }
    let launched = match seed(shared, auth, &item.id).await {
        Ok(seed) => launch(shared, item, seed).await,
        Err(err) => Err(err),
    };
    match launched {
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

async fn seed(shared: &Shared, auth: &Auth, deployment_id: &str) -> Result<Seed, AgentError> {
    let attempt = async {
        let signer = auth.read().await;
        let Some(signer) = signer.as_ref() else {
            return Ok(Signed::Refused);
        };
        shared
            .api
            .seed(&signer.agent_id, &signer.credential, deployment_id)
            .await
    };
    match timeout(SIGN_WAIT, attempt).await {
        Ok(Ok(Signed::Accepted(Some(seed)))) => Ok(seed),
        Ok(Ok(_)) => Err(AgentError::Seed("not served".into())),
        Ok(Err(err)) => Err(AgentError::Seed(err.to_string())),
        Err(_) => Err(AgentError::Seed("timed out".into())),
    }
}

async fn launch(shared: &Shared, item: &Desired, seed: Seed) -> Result<(), AgentError> {
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
        seed: Some(seed),
    };
    vm::create(&shared.root, &spec, &shared.tools)
        .await
        .map(drop)
        .map_err(|err| match err {
            AgentError::Vm(_) | AgentError::Seed(_) => err,
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
