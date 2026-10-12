use std::fs;
use std::sync::Arc;

use tempfile::TempDir;
use tokio::sync::oneshot::{self, error::TryRecvError};
use tokio::sync::RwLock;
use url::Url;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

use super::{Kind, QemuRuntime};
use crate::libs::agent::{Auth, Runtime, Signer};
use crate::libs::api::{Desired, HttpApi, ImageRef, Resources, ScenarioRef, Status, Timeouts};
use crate::libs::config::Config;
use crate::libs::state::Credential;

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
        qemu: "/nonexistent/qemu-system-x86_64".into(),
        qemu_img: "/nonexistent/qemu-img".into(),
        xorriso: "/nonexistent/xorriso".into(),
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
        check: None,
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

    runtime.destroy_all().unwrap();
    runtime.fence.take().unwrap().await.unwrap();

    assert!(runtime.consoles.is_empty());
    assert_eq!(released.try_recv(), Err(TryRecvError::Closed));
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
