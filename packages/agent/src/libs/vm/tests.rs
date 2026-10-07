use std::ffi::OsString;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::time::Duration;

use serde_json::{json, Value};
use tempfile::TempDir;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixListener;
use tokio::task::JoinHandle;

use super::{
    base_format, create, destroy, eject_seed, list, qemu_args, Record, Tools, VmSpec, GIB,
};
use crate::libs::api::Seed;
use crate::libs::config::prepare_private_dir;
use crate::libs::error::AgentError;
use crate::libs::qmp::Qmp;

fn spec(deployment_id: &str, base: &Path) -> VmSpec {
    VmSpec {
        deployment_id: deployment_id.into(),
        vm_id: format!("vm-{deployment_id}"),
        cpus: 2,
        memory_mib: 1024,
        disk_gib: 8,
        base: base.into(),
        seed: None,
    }
}

fn seed() -> Seed {
    Seed {
        user_data: "#cloud-config\n".into(),
        meta_data: "instance-id: alpha\n".into(),
    }
}

fn missing_tools() -> Tools {
    Tools {
        qemu: "/nonexistent/qemu-system-x86_64".into(),
        qemu_img: "/nonexistent/qemu-img".into(),
        xorriso: "/nonexistent/xorriso".into(),
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
        created_at: 0,
    })
}

/// A QMP server that greets one client, answers every command with `{}` and returns them.
fn qmp_server(dir: &Path) -> JoinHandle<Vec<Value>> {
    let listener = UnixListener::bind(dir.join("qmp.sock")).unwrap();
    tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let (reader, mut writer) = stream.into_split();
        let mut lines = BufReader::new(reader).lines();
        writer.write_all(b"{\"QMP\": {}}\n").await.unwrap();
        let mut seen = Vec::new();
        while let Ok(Some(line)) = lines.next_line().await {
            seen.push(serde_json::from_str(&line).unwrap());
            writer.write_all(b"{\"return\": {}}\n").await.unwrap();
        }
        seen
    })
}

fn image_info(virtual_size: u64) -> Vec<u8> {
    json!({ "format": "qcow2", "virtual-size": virtual_size })
        .to_string()
        .into_bytes()
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
fn a_seed_adds_the_cdrom_and_nothing_else() {
    let dir = Path::new("/state,beta/vms/alpha");
    let plain = spec("alpha", Path::new("/base.qcow2"));
    let mut seeded = plain.clone();
    seeded.seed = Some(seed());

    let without = strings(qemu_args(&plain, dir, true));
    let mut with = strings(qemu_args(&seeded, dir, true));

    let at = with.iter().position(|arg| arg.contains("id=seed")).unwrap() - 1;
    let cdrom: Vec<String> = with.drain(at..at + 4).collect();
    assert_eq!(
        cdrom,
        [
            "-drive",
            "if=none,id=seed,media=cdrom,readonly=on,file=/state,,beta/vms/alpha/seed.iso",
            "-device",
            "ide-cd,id=seed0,drive=seed,bus=ide.1",
        ]
    );
    assert_eq!(with, without);
    assert!(!without.iter().any(|arg| arg.contains("seed")));
}

#[test]
fn a_disk_below_the_base_virtual_size_is_refused() {
    let err = base_format(&image_info(2 * GIB + 1), 2).unwrap_err();

    assert!(
        matches!(&err, AgentError::Vm(message)
            if message.contains("disk_gib 2") && message.contains("2147483649 bytes")),
        "{err}"
    );
    assert_eq!(base_format(&image_info(2 * GIB), 2).unwrap(), "qcow2");
}

#[tokio::test]
async fn a_missing_xorriso_fails_the_start_and_leaves_no_directory() {
    let tmp = TempDir::new().unwrap();
    let root = tmp.path().join("vms");
    let mut spec = spec("alpha", Path::new("/base.qcow2"));
    spec.seed = Some(seed());

    let err = create(&root, &spec, &missing_tools()).await.unwrap_err();

    assert!(
        matches!(&err, AgentError::Seed(message) if message.contains("xorriso")),
        "{err}"
    );
    assert!(!err.to_string().contains("cloud-config"));
    assert!(list(&root).unwrap().is_empty());
}

#[tokio::test]
async fn eject_seed_sends_eject_and_removes_the_file() {
    let tmp = TempDir::new().unwrap();
    let iso = tmp.path().join("seed.iso");
    fs::write(&iso, b"").unwrap();
    let server = qmp_server(tmp.path());

    eject_seed(tmp.path()).await.unwrap();

    assert!(!iso.exists());
    assert_eq!(
        server.await.unwrap(),
        [
            json!({ "execute": "qmp_capabilities" }),
            json!({ "execute": "eject", "arguments": { "id": "seed0", "force": true } }),
        ]
    );
}

#[tokio::test]
async fn eject_seed_without_a_file_is_fine() {
    let tmp = TempDir::new().unwrap();
    let server = qmp_server(tmp.path());

    eject_seed(tmp.path()).await.unwrap();

    assert_eq!(server.await.unwrap().len(), 2);
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
            created_at: 0,
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

// Needs qemu-system-x86_64, qemu-img and xorriso on PATH, so it runs only with `-- --ignored`.
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
        xorriso: "xorriso".into(),
    };
    let mut spec = spec("alpha", &base);
    spec.cpus = 1;
    spec.memory_mib = 128;
    spec.disk_gib = 1;
    spec.seed = Some(seed());

    let vm = create(&root, &spec, &tools).await.unwrap();
    let pid = fs::read_to_string(vm.dir.join("qemu.pid")).unwrap();
    let found = list(&root).unwrap();
    let iso = vm.dir.join("seed.iso");
    let mode = fs::metadata(&iso).unwrap().permissions().mode() & 0o777;
    let unpacked = vm.dir.join("seed").exists();
    let blocks = Qmp::connect(&vm.dir.join("qmp.sock"), Duration::from_secs(5))
        .await
        .unwrap()
        .execute("query-block", None)
        .await
        .unwrap();
    eject_seed(&vm.dir).await.unwrap();
    let ejected = !iso.exists();
    destroy(&vm.dir).await.unwrap();

    assert_eq!(mode, 0o600);
    assert!(!unpacked);
    assert!(blocks
        .as_array()
        .unwrap()
        .iter()
        .any(|block| block["device"] == "seed" && block.get("inserted").is_some()));
    assert!(ejected);
    assert!(vm.record.created_at > 0);
    assert_eq!(found.len(), 1);
    assert!(found[0].alive);
    assert_eq!(found[0].record, Some(vm.record));
    assert!(fs::read_dir(&root).unwrap().next().is_none());
    let cmdline = fs::read(format!("/proc/{}/cmdline", pid.trim())).unwrap_or_default();
    assert!(!cmdline.windows(8).any(|window| window == b"vm-alpha"));
}
