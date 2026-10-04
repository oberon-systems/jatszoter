use std::ffi::OsString;
use std::fs;
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command};

use serde_json::json;
use tempfile::TempDir;

use super::{create, destroy, list, qemu_args, Record, Tools, VmSpec};
use crate::libs::config::prepare_private_dir;
use crate::libs::error::AgentError;

fn spec(deployment_id: &str, base: &Path) -> VmSpec {
    VmSpec {
        deployment_id: deployment_id.into(),
        vm_id: format!("vm-{deployment_id}"),
        cpus: 2,
        memory_mib: 1024,
        disk_gib: 8,
        base: base.into(),
    }
}

fn missing_tools() -> Tools {
    Tools {
        qemu: "/nonexistent/qemu-system-x86_64".into(),
        qemu_img: "/nonexistent/qemu-img".into(),
    }
}

fn strings(args: Vec<OsString>) -> Vec<String> {
    args.into_iter()
        .map(|arg| arg.into_string().unwrap())
        .collect()
}

fn fake(root: &Path, deployment_id: &str, vm_id: Option<&str>, pid: Option<u32>) -> PathBuf {
    let dir = root.join(deployment_id);
    fs::create_dir_all(&dir).unwrap();
    if let Some(vm_id) = vm_id {
        let record = json!({ "deployment_id": deployment_id, "vm_id": vm_id });
        fs::write(dir.join("vm.json"), record.to_string()).unwrap();
    }
    if let Some(pid) = pid {
        fs::write(dir.join("qemu.pid"), format!("{pid}\n")).unwrap();
    }
    dir
}

fn sleeper(arg0: &str) -> Child {
    Command::new("sleep").arg0(arg0).arg("60").spawn().unwrap()
}

fn reaped_pid() -> u32 {
    let mut child = Command::new("true").spawn().unwrap();
    let pid = child.id();
    child.wait().unwrap();
    pid
}

fn record(deployment_id: &str) -> Option<Record> {
    Some(Record {
        deployment_id: deployment_id.into(),
        vm_id: format!("vm-{deployment_id}"),
    })
}

#[test]
fn the_argv_for_kvm() {
    let dir = Path::new("/state/vms/alpha");

    let args = strings(qemu_args(
        &spec("alpha", Path::new("/base.qcow2")),
        dir,
        true,
    ));

    assert_eq!(
        args,
        [
            "-name",
            "vm-alpha",
            "-nodefaults",
            "-no-user-config",
            "-display",
            "none",
            "-machine",
            "q35,accel=kvm",
            "-cpu",
            "host",
            "-smp",
            "2",
            "-m",
            "1024M",
            "-drive",
            "if=virtio,format=qcow2,file=/state/vms/alpha/disk.qcow2",
            "-netdev",
            "user,id=net0",
            "-device",
            "virtio-net-pci,netdev=net0",
            "-chardev",
            "socket,id=serial0,server=on,wait=off,path=/state/vms/alpha/serial.sock",
            "-serial",
            "chardev:serial0",
            "-device",
            "virtio-serial",
            "-chardev",
            "socket,id=qga0,server=on,wait=off,path=/state/vms/alpha/qga.sock",
            "-device",
            "virtserialport,chardev=qga0,name=org.qemu.guest_agent.0",
            "-chardev",
            "socket,id=qmp0,server=on,wait=off,path=/state/vms/alpha/qmp.sock",
            "-mon",
            "chardev=qmp0,mode=control",
            "-daemonize",
            "-pidfile",
            "/state/vms/alpha/qemu.pid",
        ]
    );
}

#[test]
fn the_argv_for_tcg() {
    let dir = Path::new("/state/vms/alpha");

    let args = strings(qemu_args(
        &spec("alpha", Path::new("/base.qcow2")),
        dir,
        false,
    ));

    assert!(args
        .windows(2)
        .any(|pair| pair == ["-machine", "q35,accel=tcg"]));
    assert!(args.windows(2).any(|pair| pair == ["-cpu", "max"]));
    assert!(!args.iter().any(|arg| arg.contains("kvm") || arg == "host"));
    assert!(!args.iter().any(|arg| arg.contains("hostfwd")));
}

#[test]
fn a_comma_in_a_path_is_escaped() {
    let dir = Path::new("/state,beta/vms/alpha");

    let args = strings(qemu_args(
        &spec("alpha", Path::new("/base.qcow2")),
        dir,
        true,
    ));

    assert!(args.contains(&"if=virtio,format=qcow2,file=/state,,beta/vms/alpha/disk.qcow2".into()));
    assert!(args.contains(&"/state,beta/vms/alpha/qemu.pid".into()));
}

#[test]
fn list_sees_a_live_vm_a_dead_one_and_an_orphan() {
    let tmp = TempDir::new().unwrap();
    let root = tmp.path().join("vms");
    let mut live = sleeper("jatszoter-vm-alpha");
    fake(&root, "alpha", Some("vm-alpha"), Some(live.id()));
    fake(&root, "beta", Some("vm-beta"), Some(reaped_pid()));
    fake(&root, "gamma", None, None);
    fs::write(root.join("stray"), b"").unwrap();

    let found = list(&root).unwrap();
    live.kill().unwrap();
    live.wait().unwrap();

    let seen: Vec<_> = found
        .iter()
        .map(|vm| (vm.dir.clone(), vm.record.clone(), vm.alive))
        .collect();
    assert_eq!(
        seen,
        [
            (root.join("alpha"), record("alpha"), true),
            (root.join("beta"), record("beta"), false),
            (root.join("gamma"), None, false),
        ]
    );
}

#[test]
fn list_of_a_missing_root_is_empty() {
    let tmp = TempDir::new().unwrap();

    assert!(list(&tmp.path().join("vms")).unwrap().is_empty());
}

#[tokio::test]
async fn destroy_kills_a_live_vm_and_removes_live_dead_and_orphan() {
    let tmp = TempDir::new().unwrap();
    let root = tmp.path().join("vms");
    let mut live = sleeper("jatszoter-vm-alpha");
    let dirs = [
        fake(&root, "alpha", Some("vm-alpha"), Some(live.id())),
        fake(&root, "beta", Some("vm-beta"), Some(reaped_pid())),
        fake(&root, "gamma", None, None),
    ];

    for dir in &dirs {
        destroy(dir).await.unwrap();
    }

    assert_eq!(live.wait().unwrap().signal(), Some(9));
    assert!(dirs.iter().all(|dir| !dir.exists()));
    assert!(list(&root).unwrap().is_empty());
}

#[tokio::test]
async fn destroy_is_idempotent() {
    let tmp = TempDir::new().unwrap();
    let dir = fake(tmp.path(), "alpha", Some("vm-alpha"), None);

    destroy(&dir).await.unwrap();
    destroy(&dir).await.unwrap();

    assert!(!dir.exists());
}

#[tokio::test]
async fn a_pid_naming_an_unrelated_process_is_not_alive_and_never_killed() {
    let tmp = TempDir::new().unwrap();
    let root = tmp.path().join("vms");
    let mut unrelated = sleeper("sleep");
    let dir = fake(&root, "alpha", Some("vm-alpha"), Some(unrelated.id()));

    let found = list(&root).unwrap();
    destroy(&dir).await.unwrap();
    let running = unrelated.try_wait().unwrap().is_none();
    unrelated.kill().unwrap();
    unrelated.wait().unwrap();

    assert!(!found[0].alive);
    assert!(running);
    assert!(!dir.exists());
}

#[tokio::test]
async fn an_existing_directory_is_refused_and_kept() {
    let tmp = TempDir::new().unwrap();
    let root = tmp.path().join("vms");
    prepare_private_dir(&root).unwrap();
    let dir = fake(&root, "alpha", Some("vm-beta"), None);

    let err = create(
        &root,
        &spec("alpha", Path::new("/base.qcow2")),
        &missing_tools(),
    )
    .await
    .unwrap_err();

    assert!(
        matches!(&err, AgentError::Vm(message) if message.ends_with("already exists")),
        "{err}"
    );
    assert_eq!(
        list(&root).unwrap()[0].record,
        Some(Record {
            deployment_id: "alpha".into(),
            vm_id: "vm-beta".into(),
        })
    );
    assert!(dir.exists());
}

#[tokio::test]
async fn an_overlong_socket_path_is_refused_before_anything_is_written() {
    let tmp = TempDir::new().unwrap();
    let root = tmp.path().join("a".repeat(100)).join("vms");

    let err = create(
        &root,
        &spec("alpha", Path::new("/base.qcow2")),
        &missing_tools(),
    )
    .await
    .unwrap_err();

    assert!(
        matches!(&err, AgentError::Vm(message) if message.contains("the limit for a socket path")),
        "{err}"
    );
    assert!(fs::read_dir(tmp.path()).unwrap().next().is_none());
}

#[tokio::test]
async fn an_id_that_is_not_a_plain_word_is_refused() {
    let tmp = TempDir::new().unwrap();
    let root = tmp.path().join("vms");

    let err = create(
        &root,
        &spec("../alpha", Path::new("/base.qcow2")),
        &missing_tools(),
    )
    .await
    .unwrap_err();

    assert!(
        matches!(&err, AgentError::Vm(message) if message.contains("not a valid id")),
        "{err}"
    );
    assert!(fs::read_dir(tmp.path()).unwrap().next().is_none());
}

#[tokio::test]
async fn a_failed_start_leaves_no_directory() {
    let tmp = TempDir::new().unwrap();
    let root = tmp.path().join("vms");

    let err = create(
        &root,
        &spec("alpha", Path::new("/base.qcow2")),
        &missing_tools(),
    )
    .await
    .unwrap_err();

    assert!(
        matches!(&err, AgentError::Vm(message) if message.contains("qemu-img")),
        "{err}"
    );
    assert!(list(&root).unwrap().is_empty());
}

// Needs qemu-system-x86_64 and qemu-img on PATH, so it runs only with `-- --ignored`.
#[tokio::test]
#[ignore]
async fn a_real_vm_starts_is_listed_and_is_destroyed() {
    let tmp = TempDir::new().unwrap();
    let base = tmp.path().join("base.qcow2");
    let made = Command::new("qemu-img")
        .args(["create", "-f", "qcow2"])
        .arg(&base)
        .arg("64M")
        .output()
        .unwrap();
    assert!(made.status.success());
    let root = tmp.path().join("vms");
    let tools = Tools {
        qemu: "qemu-system-x86_64".into(),
        qemu_img: "qemu-img".into(),
    };
    let mut spec = spec("alpha", &base);
    spec.cpus = 1;
    spec.memory_mib = 128;
    spec.disk_gib = 1;

    let vm = create(&root, &spec, &tools).await.unwrap();
    let pid = fs::read_to_string(vm.dir.join("qemu.pid")).unwrap();
    let found = list(&root).unwrap();
    destroy(&vm.dir).await.unwrap();

    assert_eq!(found.len(), 1);
    assert!(found[0].alive);
    assert_eq!(found[0].record, Some(vm.record));
    assert!(fs::read_dir(&root).unwrap().next().is_none());
    let cmdline = fs::read(format!("/proc/{}/cmdline", pid.trim())).unwrap_or_default();
    assert!(!cmdline.windows(8).any(|window| window == b"vm-alpha"));
}
