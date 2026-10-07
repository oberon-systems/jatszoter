use std::fs;

use tempfile::TempDir;
use tokio::sync::oneshot::{self, error::TryRecvError};
use url::Url;

use super::QemuRuntime;
use crate::libs::agent::Runtime;
use crate::libs::api::HttpApi;
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
    };
    QemuRuntime::new(&config, HttpApi::new(config.api_url.clone()).unwrap()).unwrap()
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
