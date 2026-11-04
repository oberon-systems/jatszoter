use std::fs;
use std::io;
use std::sync::{Arc, Mutex};

use tempfile::TempDir;
use tokio::sync::oneshot::{self, error::TryRecvError};
use tokio::sync::RwLock;
use tokio::time::{sleep, timeout, Duration};
use url::Url;
use uuid::Uuid;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

use super::{reason, Kind, QemuRuntime};
use crate::libs::agent::{Auth, Runtime, Signer};
use crate::libs::api::{
    Desired, HttpApi, ImageRef, Limits, Resources, ScenarioRef, Status, Timeouts,
};
use crate::libs::config::Config;
use crate::libs::error::AgentError;
use crate::libs::state::Credential;
use crate::libs::vm::{self, OnDisk};

fn runtime(dir: &TempDir) -> QemuRuntime {
    runtime_at(dir, "https://api.example.com/")
}

fn runtime_at(dir: &TempDir, api_url: &str) -> QemuRuntime {
    let config = Config {
        api_url: Url::parse(api_url).unwrap(),
        name: "alpha".into(),
        labels: Vec::new(),
        capacity: 2,
        state_dir: dir.path().to_path_buf(),
        image_limit_bytes: 1 << 30,
        image_cache_bytes: 4 << 30,
        image_download_deadline: std::time::Duration::from_secs(60),
        image_internal_cidrs: Vec::new(),
        image_use_proxy: false,
        allow_tcg: true,
        qemu: "/nonexistent/qemu-system-x86_64".into(),
        qemu_img: "/nonexistent/qemu-img".into(),
        xorriso: "/nonexistent/xorriso".into(),
        passt: "/nonexistent/passt".into(),
        inspector_url: Url::parse("https://example.com/alpha.qcow2").unwrap(),
        inspector_digest: "0".repeat(64),
        ovmf_code: "/nonexistent/OVMF_CODE.fd".into(),
        ovmf_vars: "/nonexistent/OVMF_VARS.fd".into(),
    };
    QemuRuntime::new(&config, HttpApi::new(config.api_url.clone()).unwrap()).unwrap()
}

fn stopping(id: &str) -> Desired {
    Desired {
        id: id.into(),
        vm_id: format!("vm-{id}"),
        status: Status::Stopping,
        scenario: ScenarioRef {
            id: "alpha".into(),
            slug: "alpha".into(),
            version: 1,
        },
        image: ImageRef {
            name: "beta".into(),
            url: "https://example.com/beta.qcow2".into(),
            digest: "0".repeat(64),
        },
        resources: Resources {
            cpus: 1,
            memory_mib: 512,
            disk_gib: 4,
        },
        timeouts: Timeouts {
            bootstrap_seconds: 300,
            check_seconds: 30,
            session_minutes: 60,
        },
        limits: Limits::default(),
        check: None,
        outside: crate::libs::api::Outside::default(),
    }
}

fn checked(id: &str, check_id: &str) -> Desired {
    Desired {
        status: Status::Ready,
        check: Some(check_id.into()),
        ..stopping(id)
    }
}

fn pending(runtime: &mut QemuRuntime, id: &str, kind: Kind) -> oneshot::Receiver<()> {
    let (held, released) = oneshot::channel::<()>();
    let task = tokio::spawn(async move {
        let _held = held;
        std::future::pending::<()>().await;
    });
    runtime.tasks.insert(id.into(), task);
    runtime.kinds.insert(id.into(), kind);
    released
}

#[tokio::test]
async fn fencing_aborts_in_flight_tasks_and_destroys_every_vm() {
    let dir = tempfile::tempdir().unwrap();
    let mut runtime = runtime(&dir);
    let vm = dir.path().join("vms").join("alpha");
    fs::create_dir_all(&vm).unwrap();
    fs::write(
        vm.join("vm.json"),
        r#"{"deployment_id":"alpha","vm_id":"vm-alpha"}"#,
    )
    .unwrap();
    let (held, mut released) = oneshot::channel::<()>();
    let task = tokio::spawn(async move {
        let _held = held;
        std::future::pending::<()>().await;
    });
    runtime.tasks.insert("beta".into(), task);

    runtime.destroy_all().unwrap();
    runtime.fence.take().unwrap().await.unwrap();

    assert!(runtime.tasks.is_empty());
    assert_eq!(released.try_recv(), Err(TryRecvError::Closed));
    assert!(!vm.exists());
}

#[tokio::test]
async fn a_start_of_a_deployment_turned_stopping_is_aborted_before_the_plan() {
    let dir = tempfile::tempdir().unwrap();
    let mut runtime = runtime(&dir);
    let mut released = pending(&mut runtime, "alpha", Kind::Start);
    let auth: Auth = Arc::new(RwLock::new(None));

    runtime.reconcile(&auth, vec![stopping("alpha")]);

    assert_eq!(runtime.kinds.get("alpha"), Some(&Kind::Stop));
    runtime.tasks.remove("alpha").unwrap().await.unwrap();
    assert_eq!(released.try_recv(), Err(TryRecvError::Closed));
}

#[tokio::test]
async fn a_console_of_a_deployment_turned_stopping_is_aborted_before_the_plan() {
    let dir = tempfile::tempdir().unwrap();
    let mut runtime = runtime(&dir);
    let (held, released) = oneshot::channel::<()>();
    let task = tokio::spawn(async move {
        let _held = held;
        std::future::pending::<()>().await;
    });
    runtime.consoles.insert("alpha".into(), task);
    let auth: Auth = Arc::new(RwLock::new(None));

    runtime.reconcile(&auth, vec![stopping("alpha")]);

    assert!(runtime.consoles.is_empty());
    assert_eq!(runtime.kinds.get("alpha"), Some(&Kind::Stop));
    assert!(released.await.is_err());
}

#[tokio::test]
async fn fencing_empties_the_consoles() {
    let dir = tempfile::tempdir().unwrap();
    let mut runtime = runtime(&dir);
    let (held, mut released) = oneshot::channel::<()>();
    let task = tokio::spawn(async move {
        let _held = held;
        std::future::pending::<()>().await;
    });
    runtime.consoles.insert("alpha".into(), task);
    let cap = tokio::spawn(std::future::pending::<()>());
    runtime.caps.insert("alpha".into(), (4, cap));

    runtime.destroy_all().unwrap();
    runtime.fence.take().unwrap().await.unwrap();

    assert!(runtime.consoles.is_empty());
    assert!(runtime.caps.is_empty());
    assert_eq!(released.try_recv(), Err(TryRecvError::Closed));
}

#[tokio::test]
async fn a_live_vm_has_its_console_log_cut_to_the_limit_until_it_is_no_longer_wanted() {
    let dir = tempfile::tempdir().unwrap();
    let mut runtime = runtime(&dir);
    let vm_dir = dir.path().join("vms").join("alpha");
    fs::create_dir_all(&vm_dir).unwrap();
    let log = vm_dir.join(vm::CONSOLE_LOG);
    fs::write(&log, b"login: alpha").unwrap();
    let item = Desired {
        status: Status::Ready,
        limits: Limits { console_bytes: 7 },
        ..stopping("alpha")
    };
    let on_disk = [OnDisk {
        dir: vm_dir.clone(),
        record: None,
        alive: true,
    }];
    let auth: Auth = Arc::new(RwLock::new(None));

    runtime.keep_consoles(&auth, std::slice::from_ref(&item), &on_disk);
    let cut = timeout(Duration::from_secs(5), async {
        while fs::metadata(&log).unwrap().len() != 7 {
            sleep(Duration::from_millis(20)).await;
        }
    })
    .await;

    assert!(cut.is_ok());
    assert_eq!(fs::read(&log).unwrap(), b"login: ");
    assert_eq!(runtime.caps.get("alpha").map(|(limit, _)| *limit), Some(7));
    runtime.keep_consoles(&auth, &[], &on_disk);
    assert!(runtime.caps.is_empty());
}

#[tokio::test]
async fn a_stop_in_flight_is_left_running() {
    let dir = tempfile::tempdir().unwrap();
    let mut runtime = runtime(&dir);
    let mut released = pending(&mut runtime, "alpha", Kind::Stop);
    let auth: Auth = Arc::new(RwLock::new(None));

    runtime.reconcile(&auth, vec![stopping("alpha")]);

    assert_eq!(runtime.kinds.get("alpha"), Some(&Kind::Stop));
    assert!(!runtime.tasks["alpha"].is_finished());
    assert_eq!(released.try_recv(), Err(TryRecvError::Empty));
}

#[tokio::test]
async fn the_same_check_id_in_two_rounds_is_fetched_once() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/api/v1/agents/gamma/deployments/alpha/checks/beta"))
        .respond_with(ResponseTemplate::new(409))
        .expect(1)
        .mount(&server)
        .await;
    let dir = tempfile::tempdir().unwrap();
    let mut runtime = runtime_at(&dir, &format!("{}/", server.uri()));
    let auth: Auth = Arc::new(RwLock::new(Some(Signer {
        agent_id: "gamma".into(),
        credential: Credential {
            credential: "current:1:1790000000".into(),
            expires_at: 1_790_000_000,
            key: "delta".into(),
        },
    })));

    runtime.reconcile(&auth, vec![checked("alpha", "beta")]);
    (&mut runtime.checks.get_mut("beta").unwrap().1)
        .await
        .unwrap();
    runtime.reconcile(&auth, vec![checked("alpha", "beta")]);

    assert_eq!(runtime.checks.len(), 1);
    assert!(runtime.checks["beta"].1.is_finished());
}

#[tokio::test]
async fn a_finished_check_is_forgotten_once_the_api_stops_naming_it() {
    let dir = tempfile::tempdir().unwrap();
    let mut runtime = runtime(&dir);
    let auth: Auth = Arc::new(RwLock::new(None));
    runtime.reconcile(&auth, vec![checked("alpha", "beta")]);
    (&mut runtime.checks.get_mut("beta").unwrap().1)
        .await
        .unwrap();
    let settled = Desired {
        check: None,
        ..checked("alpha", "beta")
    };

    runtime.reconcile(&auth, vec![settled]);

    assert!(runtime.checks.is_empty());
}

#[tokio::test]
async fn a_check_of_a_deployment_turned_stopping_is_aborted_and_the_stop_is_planned() {
    let dir = tempfile::tempdir().unwrap();
    let mut runtime = runtime(&dir);
    let (held, released) = oneshot::channel::<()>();
    let task = tokio::spawn(async move {
        let _held = held;
        std::future::pending::<()>().await;
    });
    runtime.checks.insert("beta".into(), ("alpha".into(), task));
    let auth: Auth = Arc::new(RwLock::new(None));

    runtime.reconcile(&auth, vec![stopping("alpha")]);

    assert!(runtime.checks.is_empty());
    assert_eq!(runtime.kinds.get("alpha"), Some(&Kind::Stop));
    assert!(released.await.is_err());
}

#[tokio::test]
async fn fencing_empties_the_checks() {
    let dir = tempfile::tempdir().unwrap();
    let mut runtime = runtime(&dir);
    let (held, mut released) = oneshot::channel::<()>();
    let task = tokio::spawn(async move {
        let _held = held;
        std::future::pending::<()>().await;
    });
    runtime.checks.insert("beta".into(), ("alpha".into(), task));

    runtime.destroy_all().unwrap();
    runtime.fence.take().unwrap().await.unwrap();

    assert!(runtime.checks.is_empty());
    assert_eq!(released.try_recv(), Err(TryRecvError::Closed));
}

#[derive(Clone, Default)]
struct Captured(Arc<Mutex<Vec<u8>>>);

impl io::Write for Captured {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

fn logged_reason(err: &AgentError, url: &str) -> (String, String) {
    let captured = Captured::default();
    let writer = captured.clone();
    let subscriber = tracing_subscriber::fmt()
        .with_ansi(false)
        .with_writer(move || writer.clone())
        .finish();
    let shown = tracing::subscriber::with_default(subscriber, || reason(err, url));
    let log = String::from_utf8(captured.0.lock().unwrap().clone()).unwrap();
    (shown, log)
}

#[test]
fn a_failed_qemu_img_shows_a_code_and_an_id_and_logs_the_stderr_under_that_id() {
    let err = AgentError::Vm("/var/lib/alpha/vms/qemu-img exit status: 1: beta gamma".into());

    let (shown, log) = logged_reason(&err, "https://example.com/beta.qcow2");

    let id = shown
        .strip_prefix("vm-failed (error ")
        .and_then(|rest| rest.strip_suffix(')'))
        .unwrap();
    assert_eq!(Uuid::parse_str(id).unwrap().get_version_num(), 4);
    let [line] = &log.lines().collect::<Vec<_>>()[..] else {
        panic!("{log}");
    };
    assert!(line.contains(id), "{log}");
    assert!(line.contains("vm-failed"), "{log}");
    assert!(line.contains("/var/lib/alpha/vms/qemu-img"), "{log}");
    assert!(line.contains("beta gamma"), "{log}");
}

#[test]
fn the_log_of_a_failed_start_keeps_the_image_url_bare() {
    let url = "https://example.com/beta.qcow2?token=delta";
    let err = AgentError::Image(format!("{url}: refused"));

    let (shown, log) = logged_reason(&err, url);

    assert!(shown.starts_with("image-failed (error "), "{shown}");
    assert!(
        log.contains("https://example.com/beta.qcow2: refused"),
        "{log}"
    );
    assert!(!log.contains("delta"), "{log}");
}

#[test]
fn every_error_has_a_stable_code() {
    let io = io::Error::other("alpha");
    for (err, code) in [
        (AgentError::Image("alpha".into()), "image-failed"),
        (AgentError::Seed("alpha".into()), "seed-failed"),
        (AgentError::Vm("alpha".into()), "vm-failed"),
        (AgentError::Qmp("alpha".into()), "agent-failed"),
        (AgentError::Io(io), "agent-failed"),
    ] {
        assert_eq!(err.code(), code);
    }
}
