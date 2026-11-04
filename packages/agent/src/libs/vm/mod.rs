use std::ffi::OsString;
use std::fs::{self, DirBuilder, OpenOptions, Permissions};
use std::io::{ErrorKind, Write};
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use nix::errno::Errno;
use nix::sys::signal::{kill, Signal};
use nix::unistd::Pid;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::process::Command;
use tokio::time::{sleep, timeout, Instant};

use crate::libs::api::{Accelerator, Seed};
use crate::libs::config::prepare_private_dir;
use crate::libs::error::AgentError;
use crate::libs::qmp::Qmp;

pub const KVM_DEVICE: &str = "/dev/kvm";
const NO_ACCELERATOR: &str = "KVM is not usable and TCG is not allowed";
const RECORD: &str = "vm.json";
const DISK: &str = "disk.qcow2";
const PID_FILE: &str = "qemu.pid";
pub(crate) const PASST_PID: &str = "passt.pid";
const QMP: &str = "qmp.sock";
pub(crate) const NET: &str = "net.sock";
pub const SERIAL: &str = "serial.sock";
pub const CONSOLE_LOG: &str = "console.log";
pub const QGA: &str = "qga.sock";
const SEED_ISO: &str = "seed.iso";
const EFIVARS: &str = "efivars.fd";
const SEED_DIR: &str = "seed";
const SEED_DEVICE: &str = "seed0";
const GIB: u64 = 1 << 30;
const SOCKETS: [&str; 4] = [QMP, SERIAL, QGA, NET];
// sun_path holds 108 bytes, the terminating NUL included.
const MAX_SOCKET_PATH: usize = 107;
const MAX_ID: usize = 64;
const MAX_STDERR: usize = 400;
const FORMATS: [&str; 2] = ["qcow2", "raw"];
const TOOL_TIMEOUT: Duration = Duration::from_secs(60);
const QMP_TIMEOUT: Duration = Duration::from_secs(5);
const SOCKET_WAIT: Duration = Duration::from_secs(10);
const QUIT_WAIT: Duration = Duration::from_secs(10);
const KILL_WAIT: Duration = Duration::from_secs(5);
const POLL: Duration = Duration::from_millis(50);

#[derive(Debug, Clone)]
pub struct VmSpec {
    pub deployment_id: String,
    pub vm_id: String,
    pub cpus: u32,
    pub memory_mib: u32,
    pub disk_gib: u32,
    pub base: PathBuf,
    pub seed: Option<Seed>,
}

#[derive(Debug, Clone)]
pub struct Tools {
    pub qemu: PathBuf,
    pub qemu_img: PathBuf,
    pub xorriso: PathBuf,
    pub passt: PathBuf,
    pub ovmf_code: PathBuf,
    pub ovmf_vars: PathBuf,
    pub kvm_device: PathBuf,
    pub allow_tcg: bool,
}

impl Tools {
    pub fn accelerator(&self) -> Accelerator {
        if kvm_usable(&self.kvm_device) {
            Accelerator::Kvm
        } else if self.allow_tcg {
            Accelerator::Tcg
        } else {
            Accelerator::None
        }
    }

    pub fn require_accelerator(&self) -> Result<Accelerator, AgentError> {
        match self.accelerator() {
            Accelerator::None => Err(AgentError::Vm(NO_ACCELERATOR.into())),
            usable => Ok(usable),
        }
    }
}

/// The contents of `vm.json`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Record {
    pub deployment_id: String,
    pub vm_id: String,
    /// Unix seconds; absent from a `vm.json` written before the field existed.
    #[serde(default)]
    pub created_at: u64,
}

#[allow(dead_code, reason = "the fields are read by the tests")]
#[derive(Debug)]
pub struct Vm {
    pub dir: PathBuf,
    pub record: Record,
}

/// A directory under the VM root; one without a readable `vm.json` is an orphan.
#[derive(Debug)]
pub struct OnDisk {
    pub dir: PathBuf,
    pub record: Option<Record>,
    pub alive: bool,
}

#[derive(Deserialize)]
struct ImageInfo {
    format: String,
    #[serde(rename = "backing-filename")]
    backing: Option<String>,
    #[serde(rename = "virtual-size")]
    virtual_size: u64,
    #[serde(rename = "format-specific")]
    specific: Option<Value>,
}

pub fn qemu_args(spec: &VmSpec, dir: &Path, kvm: bool, firmware: &Path) -> Vec<OsString> {
    let (accel, cpu) = if kvm { ("kvm", "host") } else { ("tcg", "max") };
    let words = |items: &[&str]| items.iter().map(OsString::from).collect::<Vec<_>>();
    let chardev = |id: &str, name: &str| {
        option(
            &format!("socket,id={id},server=on,wait=off,path="),
            &dir.join(name),
        )
    };
    let mut args = words(&[
        "-name",
        &spec.vm_id,
        "-nodefaults",
        "-no-user-config",
        "-display",
        "none",
        "-machine",
        &format!("q35,accel={accel}"),
        "-cpu",
        cpu,
        "-smp",
        &spec.cpus.to_string(),
        "-m",
        &format!("{}M", spec.memory_mib),
        "-drive",
    ]);
    args.push(option(
        "if=pflash,format=raw,unit=0,readonly=on,file=",
        firmware,
    ));
    args.push("-drive".into());
    args.push(option(
        "if=pflash,format=raw,unit=1,file=",
        &dir.join(EFIVARS),
    ));
    args.push("-drive".into());
    args.push(option("if=virtio,format=qcow2,file=", &dir.join(DISK)));
    if spec.seed.is_some() {
        args.push("-drive".into());
        args.push(option(
            "if=none,id=seed,media=cdrom,readonly=on,file=",
            &dir.join(SEED_ISO),
        ));
        args.extend(words(&[
            "-device",
            &format!("ide-cd,id={SEED_DEVICE},drive=seed,bus=ide.1"),
        ]));
    }
    args.push("-netdev".into());
    args.push(option(
        "stream,id=net0,server=off,addr.type=unix,addr.path=",
        &dir.join(NET),
    ));
    args.extend(words(&[
        "-device",
        "virtio-net-pci,netdev=net0",
        "-chardev",
    ]));
    let mut serial = chardev("serial0", SERIAL);
    serial.push(option(",logfile=", &dir.join(CONSOLE_LOG)));
    serial.push(",logappend=on");
    args.push(serial);
    args.extend(words(&[
        "-serial",
        "chardev:serial0",
        "-device",
        "virtio-serial",
        "-chardev",
    ]));
    args.push(chardev("qga0", QGA));
    args.extend(words(&[
        "-device",
        "virtserialport,chardev=qga0,name=org.qemu.guest_agent.0",
        "-chardev",
    ]));
    args.push(chardev("qmp0", QMP));
    args.extend(words(&[
        "-mon",
        "chardev=qmp0,mode=control",
        "-daemonize",
        "-pidfile",
    ]));
    args.push(dir.join(PID_FILE).into_os_string());
    args
}

// --no-map-gw keeps the gateway from leading to the host's loopback, the reason passt
// replaces QEMU's user-mode network; --one-off ends passt together with QEMU.
pub(crate) fn passt_args(dir: &Path) -> Vec<OsString> {
    let mut args: Vec<OsString> = [
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
        "--tcp-ports",
        "none",
        "--udp-ports",
        "none",
        "--socket",
    ]
    .iter()
    .map(OsString::from)
    .collect();
    args.push(dir.join(NET).into_os_string());
    args.push("--pid".into());
    args.push(dir.join(PASST_PID).into_os_string());
    args
}

fn kvm_usable(device: &Path) -> bool {
    OpenOptions::new()
        .read(true)
        .write(true)
        .open(device)
        .is_ok()
}

pub async fn create(root: &Path, spec: &VmSpec, tools: &Tools) -> Result<Vm, AgentError> {
    for id in [&spec.deployment_id, &spec.vm_id] {
        if !valid_id(id) {
            return Err(AgentError::Vm(format!("{id:?} is not a valid id")));
        }
    }
    let dir = root.join(&spec.deployment_id);
    for name in SOCKETS {
        let path = dir.join(name);
        if path.as_os_str().len() > MAX_SOCKET_PATH {
            return Err(AgentError::Vm(format!(
                "{} is longer than {MAX_SOCKET_PATH} bytes, the limit for a socket path; \
                 use a shorter state directory",
                path.display()
            )));
        }
    }
    let accelerator = tools.require_accelerator()?;
    prepare_private_dir(root)?;
    match DirBuilder::new().mode(0o700).create(&dir) {
        Err(err) if err.kind() == ErrorKind::AlreadyExists => {
            return Err(AgentError::Vm(format!("{} already exists", dir.display())));
        }
        other => other?,
    }
    let record = Record {
        deployment_id: spec.deployment_id.clone(),
        vm_id: spec.vm_id.clone(),
        created_at: SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |elapsed| elapsed.as_secs()),
    };
    tracing::info!(?accelerator, "starting the VM");
    if let Err(err) = start(&dir, spec, &record, tools, accelerator).await {
        if let Err(cleanup) = destroy(&dir).await {
            tracing::warn!(dir = %dir.display(), error = %cleanup, "cleanup after a failed start");
        }
        return Err(err);
    }
    Ok(Vm { dir, record })
}

async fn start(
    dir: &Path,
    spec: &VmSpec,
    record: &Record,
    tools: &Tools,
    accelerator: Accelerator,
) -> Result<(), AgentError> {
    write_private(
        &dir.join(RECORD),
        &serde_json::to_vec(record).map_err(|err| AgentError::Vm(err.to_string()))?,
    )?;
    if let Some(seed) = &spec.seed {
        pack(dir, seed, &tools.xorriso)
            .await
            .map_err(|err| match err {
                AgentError::Vm(message) => AgentError::Seed(message),
                other => AgentError::Seed(other.to_string()),
            })?;
    }

    let vars = fs::read(&tools.ovmf_vars)
        .map_err(|err| AgentError::Vm(format!("{}: {err}", tools.ovmf_vars.display())))?;
    write_private(&dir.join(EFIVARS), &vars)?;
    let base = std::path::absolute(&spec.base)?;
    let info = run(Command::new(&tools.qemu_img)
        .args(["info", "--output=json"])
        .arg(&base))
    .await?;
    let format = base_format(&info, spec.disk_gib)?;
    run(Command::new(&tools.qemu_img)
        .args(["create", "-f", "qcow2", "-F", format.as_str(), "-b"])
        .arg(&base)
        .arg(dir.join(DISK))
        .arg(format!("{}G", spec.disk_gib)))
    .await?;
    run(Command::new(&tools.passt).args(passt_args(dir))).await?;
    let kvm = accelerator == Accelerator::Kvm;
    let args = qemu_args(spec, dir, kvm, &tools.ovmf_code);
    run(Command::new(&tools.qemu).args(args)).await?;

    let socket = dir.join(QMP);
    let deadline = Instant::now() + SOCKET_WAIT;
    while !socket.exists() {
        if Instant::now() >= deadline {
            return Err(AgentError::Vm(format!("QEMU did not open {QMP}")));
        }
        sleep(POLL).await;
    }
    let status = Qmp::connect(&socket, QMP_TIMEOUT)
        .await?
        .execute("query-status", None)
        .await?;
    match status.get("status").and_then(Value::as_str) {
        Some("running") => Ok(()),
        other => Err(AgentError::Vm(format!(
            "QEMU reports {other:?}, not running"
        ))),
    }
}

fn write_private(path: &Path, content: &[u8]) -> Result<(), AgentError> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)?;
    file.write_all(content)?;
    file.sync_all()?;
    Ok(())
}

async fn pack(dir: &Path, seed: &Seed, xorriso: &Path) -> Result<(), AgentError> {
    let source = dir.join(SEED_DIR);
    DirBuilder::new().mode(0o700).create(&source)?;
    write_private(&source.join("user-data"), seed.user_data.as_bytes())?;
    write_private(&source.join("meta-data"), seed.meta_data.as_bytes())?;
    let iso = dir.join(SEED_ISO);
    run(Command::new(xorriso)
        .args(["-as", "mkisofs", "-quiet", "-o"])
        .arg(&iso)
        .args(["-V", "cidata", "-J", "-r"])
        .arg(&source))
    .await?;
    fs::remove_dir_all(&source)?;
    fs::set_permissions(&iso, Permissions::from_mode(0o600))?;
    Ok(())
}

/// Empties the seed CD-ROM and deletes `seed.iso`; an empty tray and a missing file are fine.
pub async fn eject_seed(dir: &Path) -> Result<(), AgentError> {
    Qmp::connect(&dir.join(QMP), QMP_TIMEOUT)
        .await?
        .execute("eject", Some(json!({ "id": SEED_DEVICE, "force": true })))
        .await?;
    match fs::remove_file(dir.join(SEED_ISO)) {
        Err(err) if err.kind() == ErrorKind::NotFound => Ok(()),
        other => Ok(other?),
    }
}

pub fn list(root: &Path) -> Result<Vec<OnDisk>, AgentError> {
    let entries = match fs::read_dir(root) {
        Err(err) if err.kind() == ErrorKind::NotFound => return Ok(Vec::new()),
        other => other?,
    };
    let mut found = Vec::new();
    for entry in entries {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let dir = entry.path();
        let record = read_record(&dir);
        let alive = record
            .as_ref()
            .is_some_and(|record| owner(&dir, &record.vm_id).is_some());
        found.push(OnDisk { dir, record, alive });
    }
    found.sort_by(|a, b| a.dir.cmp(&b.dir));
    Ok(found)
}

pub async fn destroy(dir: &Path) -> Result<(), AgentError> {
    let asked = quit(&dir.join(QMP)).await;
    if let Some(record) = read_record(dir) {
        stop(dir, &record.vm_id, asked).await?;
    }
    stop_passt(dir)?;
    match tokio::fs::remove_dir_all(dir).await {
        Err(err) if err.kind() == ErrorKind::NotFound => Ok(()),
        other => Ok(other?),
    }
}

async fn quit(socket: &Path) -> bool {
    let attempt = async {
        Qmp::connect(socket, QMP_TIMEOUT)
            .await?
            .execute("quit", None)
            .await
    };
    timeout(QMP_TIMEOUT, attempt)
        .await
        .is_ok_and(|reply| reply.is_ok())
}

async fn stop(dir: &Path, vm_id: &str, asked: bool) -> Result<(), AgentError> {
    let Some(pid) = owner(dir, vm_id) else {
        return Ok(());
    };
    if asked && exited(dir, vm_id, QUIT_WAIT).await {
        return Ok(());
    }
    match kill(pid, Signal::SIGKILL) {
        Ok(()) | Err(Errno::ESRCH) => {}
        Err(err) => return Err(AgentError::Vm(format!("kill {pid}: {err}"))),
    }
    if exited(dir, vm_id, KILL_WAIT).await {
        Ok(())
    } else {
        Err(AgentError::Vm(format!("QEMU {pid} outlived SIGKILL")))
    }
}

fn stop_passt(dir: &Path) -> Result<(), AgentError> {
    let socket = dir.join(NET);
    let Some(pid) = running(
        &dir.join(PASST_PID),
        "--socket",
        socket.as_os_str().as_bytes(),
    ) else {
        return Ok(());
    };
    match kill(pid, Signal::SIGKILL) {
        Ok(()) | Err(Errno::ESRCH) => Ok(()),
        Err(err) => Err(AgentError::Vm(format!("kill passt {pid}: {err}"))),
    }
}

async fn exited(dir: &Path, vm_id: &str, wait: Duration) -> bool {
    let deadline = Instant::now() + wait;
    loop {
        if owner(dir, vm_id).is_none() {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        sleep(POLL).await;
    }
}

pub fn read_record(dir: &Path) -> Option<Record> {
    serde_json::from_slice(&fs::read(dir.join(RECORD)).ok()?).ok()
}

/// The pid in `qemu.pid`, if that process still runs this VM: a reused pid is not ours.
fn owner(dir: &Path, vm_id: &str) -> Option<Pid> {
    running(&dir.join(PID_FILE), "-name", vm_id.as_bytes())
}

/// The pid in `pid_file`, if that process has `flag` followed by exactly `value` in its argv.
fn running(pid_file: &Path, flag: &str, value: &[u8]) -> Option<Pid> {
    let pid: i32 = fs::read_to_string(pid_file).ok()?.trim().parse().ok()?;
    if pid <= 0 || value.is_empty() {
        return None;
    }
    let cmdline = fs::read(format!("/proc/{pid}/cmdline")).ok()?;
    let args: Vec<&[u8]> = cmdline.split(|&byte| byte == 0).collect();
    args.windows(2)
        .any(|pair| pair[0] == flag.as_bytes() && pair[1] == value)
        .then_some(Pid::from_raw(pid))
}

fn base_format(info: &[u8], disk_gib: u32) -> Result<String, AgentError> {
    let info: ImageInfo = serde_json::from_slice(info)
        .map_err(|err| AgentError::Vm(format!("qemu-img info: {err}")))?;
    // A base naming another host file would hand that file to the guest.
    let data_file = info
        .specific
        .as_ref()
        .and_then(|specific| specific.pointer("/data/data-file"))
        .is_some();
    if info.backing.is_some() || data_file {
        return Err(AgentError::Vm(
            "the base image refers to another file".into(),
        ));
    }
    if !FORMATS.contains(&info.format.as_str()) {
        return Err(AgentError::Vm(format!(
            "base image format {:?} is not one of {}",
            info.format,
            FORMATS.join(", ")
        )));
    }
    if u64::from(disk_gib) * GIB < info.virtual_size {
        return Err(AgentError::Vm(format!(
            "disk_gib {disk_gib} is below the base image's virtual size of {} bytes",
            info.virtual_size
        )));
    }
    Ok(info.format)
}

async fn run(command: &mut Command) -> Result<Vec<u8>, AgentError> {
    let program = command
        .as_std()
        .get_program()
        .to_string_lossy()
        .into_owned();
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let output = timeout(TOOL_TIMEOUT, command.output())
        .await
        .map_err(|_| {
            AgentError::Vm(format!(
                "{program} did not finish in {} s",
                TOOL_TIMEOUT.as_secs()
            ))
        })?
        .map_err(|err| AgentError::Vm(format!("{program}: {err}")))?;
    if output.status.success() {
        Ok(output.stdout)
    } else {
        Err(AgentError::Vm(format!(
            "{program} {}: {}",
            output.status,
            cut(&output.stderr)
        )))
    }
}

fn cut(stderr: &[u8]) -> String {
    String::from_utf8_lossy(&stderr[..stderr.len().min(MAX_STDERR)])
        .trim()
        .to_owned()
}

pub fn valid_id(id: &str) -> bool {
    (1..=MAX_ID).contains(&id.len())
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_'))
}

// QEMU splits option strings on commas; a comma inside a value is written twice.
fn option(prefix: &str, path: &Path) -> OsString {
    let mut bytes = prefix.as_bytes().to_vec();
    for &byte in path.as_os_str().as_bytes() {
        bytes.push(byte);
        if byte == b',' {
            bytes.push(b',');
        }
    }
    OsString::from_vec(bytes)
}

#[cfg(test)]
mod tests;
