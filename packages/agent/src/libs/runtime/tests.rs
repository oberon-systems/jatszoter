use std::fs;
use std::sync::Arc;

use tempfile::TempDir;
use tokio::sync::oneshot::{self, error::TryRecvError};
use tokio::sync::RwLock;
use url::Url;

use super::{Kind, QemuRuntime};
use crate::libs::agent::{Auth, Runtime};
use crate::libs::api::{Desired, HttpApi, ImageRef, Resources, ScenarioRef, Status, Timeouts};
use crate::libs::config::Config;

fn runtime(dir: &TempDir) -> QemuRuntime {
    let config = Config {
        api_url: Url::parse("https://api.example.com/").unwrap(),
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
