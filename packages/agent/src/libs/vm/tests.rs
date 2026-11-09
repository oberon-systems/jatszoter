use std::collections::BTreeMap;
use std::ffi::OsString;
use std::fs;
use std::io;
use std::net::Ipv4Addr;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Value};
use tempfile::TempDir;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixListener;
use tokio::task::JoinHandle;

use super::{
    base_format, create, destroy, dns_host, eject_seed, forwarding_passt_args, free_ports,
    inspect_args, list, passt_args_with, power_off, qemu_args, Record, Tools, VmSpec,
    ANSWERS_SERIAL, DISK_SERIAL, GIB,
};
use crate::libs::api::{Accelerator, Seed};
use crate::libs::config::prepare_private_dir;
use crate::libs::error::AgentError;
use crate::libs::qmp::Qmp;

const FIRMWARE: &str = "/ovmf/OVMF_CODE.fd";

fn spec(deployment_id: &str, base: &Path) -> VmSpec {
    VmSpec {
        deployment_id: deployment_id.into(),
        vm_id: format!("vm-{deployment_id}"),
        cpus: 2,
        memory_mib: 1024,
        disk_gib: 8,
        base: base.into(),
        seed: None,
        ports: Vec::new(),
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
        passt: "/nonexistent/passt".into(),
        ovmf_code: "/nonexistent/OVMF_CODE.fd".into(),
        ovmf_vars: "/nonexistent/OVMF_VARS.fd".into(),
        kvm_device: "/nonexistent/kvm".into(),
        allow_tcg: true,
    }
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

fn vars_template(dir: &Path) -> PathBuf {
    let path = dir.join("OVMF_VARS.fd");
    fs::write(&path, b"vars").unwrap();
    path
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

/// A shell that waits on its stdin with `args` in its argv, standing in for QEMU or passt.
fn holder(args: &[&str]) -> Child {
    Command::new("sh")
        .args(["-c", "read _", "sh"])
        .args(args)
        .stdin(Stdio::piped())
        .spawn()
        .unwrap()
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
        forwards: BTreeMap::new(),
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
        Path::new(FIRMWARE),
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
            "if=pflash,format=raw,unit=0,readonly=on,file=/ovmf/OVMF_CODE.fd",
            "-drive",
            "if=pflash,format=raw,unit=1,file=/state/vms/alpha/efivars.fd",
            "-drive",
            "if=virtio,format=qcow2,file=/state/vms/alpha/disk.qcow2",
            "-netdev",
            "stream,id=net0,server=off,addr.type=unix,addr.path=/state/vms/alpha/net.sock",
            "-device",
            "virtio-net-pci,netdev=net0",
            "-chardev",
            "socket,id=serial0,server=on,wait=off,path=/state/vms/alpha/serial.sock,\
             logfile=/state/vms/alpha/console.log,logappend=on",
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
        Path::new(FIRMWARE),
    ));

    assert!(args
        .windows(2)
        .any(|pair| pair == ["-machine", "q35,accel=tcg"]));
    assert!(args.windows(2).any(|pair| pair == ["-cpu", "max"]));
    assert!(!args.iter().any(|arg| arg.contains("kvm") || arg == "host"));
    assert!(!args.iter().any(|arg| arg.contains("hostfwd")));
}

#[test]
fn passt_keeps_the_gateway_off_the_host_loopback() {
    let args = strings(passt_args_with(
        Path::new("/state/vms/alpha"),
        &BTreeMap::new(),
        Some(Ipv4Addr::new(127, 0, 0, 53)),
    ));

    assert_eq!(
        args,
        [
            "--one-off",
            "--no-map-gw",
            "--ipv4-only",
            "--address",
            "10.0.2.15",
            "--netmask",
            "24",
            "--gateway",
            "10.0.2.2",
            "--dns",
            "10.0.2.3",
            "--dns-forward",
            "10.0.2.3",
            "--dns-host",
            "127.0.0.53",
            "--tcp-ports",
            "none",
            "--udp-ports",
            "none",
            "--socket",
            "/state/vms/alpha/net.sock",
            "--pid",
            "/state/vms/alpha/passt.pid",
        ]
    );
}

#[test]
fn the_dns_host_is_the_first_ipv4_nameserver() {
    let resolv_conf = "# resolved\nnameserver ::1\nnameserver 127.0.0.53\nnameserver 192.0.2.53\n";

    assert_eq!(dns_host(resolv_conf), Some(Ipv4Addr::new(127, 0, 0, 53)));
    assert_eq!(dns_host("search example.com\n"), None);
}

#[test]
fn a_comma_in_a_path_is_escaped() {
    let dir = Path::new("/state,beta/vms/alpha");

    let args = strings(qemu_args(
        &spec("alpha", Path::new("/base.qcow2")),
        dir,
        true,
        Path::new(FIRMWARE),
    ));

    assert!(args.contains(&"if=virtio,format=qcow2,file=/state,,beta/vms/alpha/disk.qcow2".into()));
    assert!(
        args.contains(&"if=pflash,format=raw,unit=1,file=/state,,beta/vms/alpha/efivars.fd".into())
    );
    assert!(args.contains(&"/state,beta/vms/alpha/qemu.pid".into()));
    assert!(args.contains(
        &"stream,id=net0,server=off,addr.type=unix,addr.path=/state,,beta/vms/alpha/net.sock"
            .into()
    ));
    assert!(args.contains(
        &"socket,id=serial0,server=on,wait=off,path=/state,,beta/vms/alpha/serial.sock,\
          logfile=/state,,beta/vms/alpha/console.log,logappend=on"
            .into()
    ));
}

#[test]
fn a_seed_adds_the_cdrom_and_nothing_else() {
    let dir = Path::new("/state,beta/vms/alpha");
    let plain = spec("alpha", Path::new("/base.qcow2"));
    let mut seeded = plain.clone();
    seeded.seed = Some(seed());

    let without = strings(qemu_args(&plain, dir, true, Path::new(FIRMWARE)));
    let mut with = strings(qemu_args(&seeded, dir, true, Path::new(FIRMWARE)));

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
    let mut live = holder(&["-name", "vm-alpha"]);
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
    let mut live = holder(&["-name", "vm-alpha"]);
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
async fn destroy_kills_the_vm_passt_and_spares_an_unrelated_one() {
    let tmp = TempDir::new().unwrap();
    let root = tmp.path().join("vms");
    let alpha = fake(&root, "alpha", Some("vm-alpha"), None);
    let beta = fake(&root, "beta", Some("vm-beta"), None);
    let mut passt = holder(&["--socket", alpha.join("net.sock").to_str().unwrap()]);
    let mut unrelated = sleeper("passt");
    fs::write(alpha.join("passt.pid"), format!("{}\n", passt.id())).unwrap();
    fs::write(beta.join("passt.pid"), format!("{}\n", unrelated.id())).unwrap();

    destroy(&alpha).await.unwrap();
    destroy(&beta).await.unwrap();
    let running = unrelated.try_wait().unwrap().is_none();
    unrelated.kill().unwrap();
    unrelated.wait().unwrap();

    assert_eq!(passt.wait().unwrap().signal(), Some(9));
    assert!(running);
    assert!(!alpha.exists() && !beta.exists());
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
async fn a_process_that_only_mentions_the_vm_id_is_not_ours() {
    let tmp = TempDir::new().unwrap();
    let root = tmp.path().join("vms");
    let mut near = holder(&["-name", "vm-alpha-2", "jatszoter-vm-alpha"]);
    let dir = fake(&root, "alpha", Some("vm-alpha"), Some(near.id()));

    let found = list(&root).unwrap();
    destroy(&dir).await.unwrap();
    let running = near.try_wait().unwrap().is_none();
    near.kill().unwrap();
    near.wait().unwrap();

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
            forwards: BTreeMap::new(),
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
    let mut tools = missing_tools();
    tools.ovmf_vars = vars_template(tmp.path());

    let err = create(&root, &spec("alpha", Path::new("/base.qcow2")), &tools)
        .await
        .unwrap_err();

    assert!(
        matches!(&err, AgentError::Vm(message) if message.contains("qemu-img")),
        "{err}"
    );
    assert!(list(&root).unwrap().is_empty());
}

#[tokio::test]
async fn a_missing_vars_template_fails_the_start_and_leaves_no_directory() {
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
        matches!(&err, AgentError::Vm(message) if message.contains("OVMF_VARS.fd")),
        "{err}"
    );
    assert!(list(&root).unwrap().is_empty());
}

#[test]
fn kvm_is_preferred_and_tcg_needs_the_setting() {
    let tmp = TempDir::new().unwrap();
    let device = tmp.path().join("kvm");
    fs::write(&device, b"").unwrap();
    let mut tools = missing_tools();

    assert_eq!(tools.accelerator(), Accelerator::Tcg);
    tools.allow_tcg = false;
    assert_eq!(tools.accelerator(), Accelerator::None);
    tools.kvm_device = device;
    assert_eq!(tools.accelerator(), Accelerator::Kvm);
}

#[tokio::test]
async fn a_start_without_kvm_is_refused_unless_tcg_is_allowed() {
    let tmp = TempDir::new().unwrap();
    let root = tmp.path().join("vms");
    let mut tools = missing_tools();
    tools.allow_tcg = false;

    let err = create(&root, &spec("alpha", Path::new("/base.qcow2")), &tools)
        .await
        .unwrap_err();

    assert_eq!(
        err.to_string(),
        "vm: KVM is not usable and TCG is not allowed"
    );
    assert!(!root.exists());
}

#[tokio::test]
async fn an_allowed_tcg_start_is_logged() {
    let tmp = TempDir::new().unwrap();
    let root = tmp.path().join("vms");
    let mut tools = missing_tools();
    tools.ovmf_vars = vars_template(tmp.path());
    let captured = Captured::default();
    let writer = captured.clone();
    let subscriber = tracing_subscriber::fmt()
        .with_ansi(false)
        .with_writer(move || writer.clone())
        .finish();
    let guard = tracing::subscriber::set_default(subscriber);

    let err = create(&root, &spec("alpha", Path::new("/base.qcow2")), &tools)
        .await
        .unwrap_err();
    drop(guard);

    assert!(
        matches!(&err, AgentError::Vm(message) if message.contains("qemu-img")),
        "{err}"
    );
    let log = String::from_utf8(captured.0.lock().unwrap().clone()).unwrap();
    assert!(log.contains("accelerator=Tcg"), "{log}");
}

// Needs qemu-system-x86_64, qemu-img, xorriso, passt and OVMF, so it runs only with `-- --ignored`.
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
        passt: "passt".into(),
        ovmf_code: "/usr/share/OVMF/OVMF_CODE_4M.fd".into(),
        ovmf_vars: "/usr/share/OVMF/OVMF_VARS_4M.fd".into(),
        kvm_device: "/dev/kvm".into(),
        allow_tcg: true,
    };
    let mut spec = spec("alpha", &base);
    spec.cpus = 1;
    spec.memory_mib = 128;
    spec.disk_gib = 1;
    spec.seed = Some(seed());

    let vm = create(&root, &spec, &tools).await.unwrap();
    let pid = fs::read_to_string(vm.dir.join("qemu.pid")).unwrap();
    let passt_pid = fs::read_to_string(vm.dir.join("passt.pid")).unwrap();
    let found = list(&root).unwrap();
    let iso = vm.dir.join("seed.iso");
    let mode = fs::metadata(&iso).unwrap().permissions().mode() & 0o777;
    let unpacked = vm.dir.join("seed").exists();
    let vars = vm.dir.join("efivars.fd");
    let vars_mode = fs::metadata(&vars).unwrap().permissions().mode() & 0o777;
    let vars_copied = fs::read(&vars).unwrap() == fs::read(&tools.ovmf_vars).unwrap();
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
    assert_eq!(vars_mode, 0o600);
    assert!(vars_copied);
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
    let passt_cmdline = fs::read(format!("/proc/{}/cmdline", passt_pid.trim())).unwrap_or_default();
    assert!(!passt_cmdline.windows(8).any(|window| window == b"net.sock"));
}

#[test]
fn a_forward_binds_the_host_loopback_and_replaces_none() {
    let forwards = BTreeMap::from([(22, 40022), (80, 40080)]);

    let args = strings(forwarding_passt_args(
        Path::new("/state/vms/alpha"),
        &forwards,
    ));

    let at = args.iter().position(|arg| arg == "--tcp-ports").unwrap();
    assert_eq!(
        args[at..at + 6],
        [
            "--tcp-ports",
            "127.0.0.1/40022:22",
            "--tcp-ports",
            "127.0.0.1/40080:80",
            "--udp-ports",
            "none",
        ]
    );
    assert!(args.contains(&"--no-map-gw".to_owned()));
    assert_eq!(args.iter().filter(|arg| *arg == "none").count(), 1);
}

#[test]
fn each_guest_port_gets_one_host_port_of_its_own() {
    let forwards = free_ports(&[80, 22, 80]).unwrap();

    assert_eq!(forwards.keys().copied().collect::<Vec<_>>(), [22, 80]);
    assert_ne!(forwards[&22], forwards[&80]);
}

#[test]
fn a_record_from_before_the_forwards_reads_with_none() {
    let record: Record = serde_json::from_str(r#"{"deployment_id":"a","vm_id":"b"}"#).unwrap();

    assert!(record.forwards.is_empty());
}

#[tokio::test]
async fn a_guest_that_cannot_be_asked_is_killed_and_its_disk_stays() {
    let tmp = TempDir::new().unwrap();
    let root = tmp.path().join("vms");
    let mut live = holder(&["-name", "vm-alpha"]);
    let dir = fake(&root, "alpha", Some("vm-alpha"), Some(live.id()));

    power_off(&dir, Duration::from_millis(50)).await.unwrap();

    assert_eq!(live.wait().unwrap().signal(), Some(9));
    assert!(dir.exists());
}

#[tokio::test]
async fn powering_off_what_has_no_record_changes_nothing() {
    let tmp = TempDir::new().unwrap();
    let root = tmp.path().join("vms");
    let dir = fake(&root, "gamma", None, None);

    power_off(&dir, Duration::from_millis(50)).await.unwrap();

    assert!(dir.exists());
}

#[test]
fn the_inspector_has_no_network_and_sees_the_disk_read_only() {
    let args = strings(inspect_args(
        Path::new("/vms/alpha/inspect"),
        Path::new("/vms/alpha/disk.qcow2"),
        true,
        Path::new(FIRMWARE),
    ));

    assert!(!args
        .iter()
        .any(|arg| arg.starts_with("-net") || arg.contains("netdev")));
    assert!(args.contains(
        &"if=none,id=disk,format=qcow2,readonly=on,file=/vms/alpha/disk.qcow2".to_owned()
    ));
    assert!(args.contains(&"virtio-blk-pci,drive=system,bootindex=1".to_owned()));
    assert!(args.contains(&format!("virtio-blk-pci,drive=disk,serial={DISK_SERIAL}")));
    assert!(args.contains(&format!(
        "virtio-blk-pci,drive=answers,serial={ANSWERS_SERIAL}"
    )));
    assert!(args.contains(&"-no-reboot".to_owned()));
    assert!(args.contains(&"q35,accel=kvm".to_owned()));
}
