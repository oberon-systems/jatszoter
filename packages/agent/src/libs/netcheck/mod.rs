use std::ffi::OsString;
use std::fs;
use std::io::{Read, Write};
use std::net::{Ipv4Addr, TcpListener};
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::process::Command;
use std::thread::sleep;
use std::time::{Duration, Instant};

use nix::sys::signal::{kill, Signal};
use nix::unistd::Pid;
use tempfile::TempDir;

use crate::libs::config::{prepare_private_dir, NetConfig};
use crate::libs::vm::{passt_args, NET, PASST_PID};

pub const MIN_VERSION: &str = "2026_10_02";
pub const RUNBOOK: &str = "docs/project/09-guest-network.md";
const VERSION: &str = "passt is 2026_10_02 or newer";
const STARTS: &str = "passt starts and answers a guest";
const DNS_ANSWERS: &str = "DNS answers on 10.0.2.3";
const GATEWAY_WALLED: &str = "10.0.2.2 does not reach the host loopback";
const LOOPBACK_WALLED: &str = "127.0.0.1 from the guest does not reach the host loopback";
const GUEST: Ipv4Addr = Ipv4Addr::new(10, 0, 2, 15);
const GATEWAY: Ipv4Addr = Ipv4Addr::new(10, 0, 2, 2);
const DNS: Ipv4Addr = Ipv4Addr::new(10, 0, 2, 3);
const GUEST_MAC: [u8; 6] = [0x52, 0x54, 0x00, 0x12, 0x34, 0x56];
const PASST_MAC: [u8; 6] = [0x9a, 0x55, 0x9a, 0x55, 0x9a, 0x55];
const WAIT: Duration = Duration::from_secs(3);
const RESEND: Duration = Duration::from_millis(500);
const POLL: Duration = Duration::from_millis(50);
const MAX_FRAME: usize = 1 << 17;
const DNS_ID: [u8; 2] = [0x4a, 0x7a];

#[derive(Debug)]
pub struct Outcome {
    pub name: &'static str,
    pub result: Result<(), String>,
}

/// Runs passt as the agent would and plays its guest; every check is reported, none stops the rest.
pub fn run(config: &NetConfig) -> Vec<Outcome> {
    let mut outcomes = vec![Outcome {
        name: VERSION,
        result: version(&config.passt),
    }];
    let mut passt = match prepare_private_dir(&config.state_dir)
        .map_err(|err| err.to_string())
        .and_then(|()| start(&config.passt, &config.state_dir, passt_args))
        .and_then(|mut passt| passt.answers_arp().then_some(passt).ok_or_else(silent))
    {
        Ok(passt) => passt,
        Err(why) => {
            outcomes.push(Outcome {
                name: STARTS,
                result: Err(why),
            });
            for name in [DNS_ANSWERS, GATEWAY_WALLED, LOOPBACK_WALLED] {
                outcomes.push(Outcome {
                    name,
                    result: Err("not run: no guest traffic goes through passt".into()),
                });
            }
            return outcomes;
        }
    };
    outcomes.push(Outcome {
        name: STARTS,
        result: Ok(()),
    });
    outcomes.push(Outcome {
        name: DNS_ANSWERS,
        result: passt
            .answers_dns()
            .then_some(())
            .ok_or_else(|| "no answer; check the host resolver in /etc/resolv.conf".into()),
    });
    outcomes.push(Outcome {
        name: GATEWAY_WALLED,
        result: passt.walled_off(GATEWAY),
    });
    outcomes.push(Outcome {
        name: LOOPBACK_WALLED,
        result: passt.walled_off(Ipv4Addr::LOCALHOST),
    });
    outcomes
}

fn silent() -> String {
    "passt took the connection but answered no ARP request; is its AppArmor or SELinux \
     profile in place?"
        .into()
}

fn version(passt: &Path) -> Result<(), String> {
    let output = Command::new(passt)
        .arg("--version")
        .output()
        .map_err(|err| format!("{}: {err}", passt.display()))?;
    let text = String::from_utf8_lossy(&output.stdout);
    check_version(text.lines().next().unwrap_or_default().trim())
}

pub fn check_version(line: &str) -> Result<(), String> {
    match (release_date(line), release_date(MIN_VERSION)) {
        (Some(date), Some(min)) if date >= min => Ok(()),
        (Some(_), _) => Err(format!("{line:?} is older than {MIN_VERSION}")),
        (None, _) => Err(format!(
            "{line:?} names no release date; build {MIN_VERSION} or newer"
        )),
    }
}

/// The date of a passt release as YYYYMMDD: upstream prints `2026_10_02.cba3570`,
/// RPM builds `0^20250217.ga1e48a0-1.el9`.
fn release_date(version: &str) -> Option<u32> {
    version
        .split(|c: char| !(c.is_ascii_digit() || c == '_'))
        .find_map(|token| {
            let digits = match token.as_bytes() {
                [_, _, _, _, b'_', _, _, b'_', _, _] => token.replace('_', ""),
                bytes if bytes.len() == 8 => token.to_owned(),
                _ => return None,
            };
            let date: u32 = digits.parse().ok()?;
            (20_200_000..21_000_000).contains(&date).then_some(date)
        })
}

pub struct Passt {
    _dir: TempDir,
    pid: Option<Pid>,
    stream: UnixStream,
}

impl Drop for Passt {
    fn drop(&mut self) {
        if let Some(pid) = self.pid {
            let _ = kill(pid, Signal::SIGKILL);
        }
    }
}

/// Starts passt with `args` in a private directory under `root` and connects to it as QEMU does.
pub fn start(
    passt: &Path,
    root: &Path,
    args: impl FnOnce(&Path) -> Vec<OsString>,
) -> Result<Passt, String> {
    let dir = tempfile::Builder::new()
        .prefix("netcheck-")
        .tempdir_in(root)
        .map_err(|err| format!("{}: {err}", root.display()))?;
    let output = Command::new(passt)
        .args(args(dir.path()))
        .output()
        .map_err(|err| format!("{}: {err}", passt.display()))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!(
            "{} {}: {}",
            passt.display(),
            output.status,
            stderr.lines().last().unwrap_or_default()
        ));
    }
    let pid = fs::read_to_string(dir.path().join(PASST_PID))
        .ok()
        .and_then(|pid| pid.trim().parse().ok())
        .map(Pid::from_raw);
    match UnixStream::connect(dir.path().join(NET)) {
        Ok(stream) => Ok(Passt {
            _dir: dir,
            pid,
            stream,
        }),
        Err(err) => {
            if let Some(pid) = pid {
                let _ = kill(pid, Signal::SIGKILL);
            }
            Err(format!("{}: {err}", dir.path().join(NET).display()))
        }
    }
}

impl Passt {
    pub fn answers_arp(&mut self) -> bool {
        let mut request = vec![0, 1, 0x08, 0x00, 6, 4, 0, 1];
        request.extend(GUEST_MAC);
        request.extend(GUEST.octets());
        request.extend([0; 6]);
        request.extend(GATEWAY.octets());
        let frame = ethernet([0xff; 6], [0x08, 0x06], &request);
        self.exchange(&frame, |reply| {
            reply.get(12..14) == Some(&[0x08, 0x06][..])
                && reply.get(20..22) == Some(&[0, 2][..])
                && reply.get(28..32) == Some(&GATEWAY.octets()[..])
        })
    }

    pub fn answers_dns(&mut self) -> bool {
        let mut query = DNS_ID.to_vec();
        query.extend([0x01, 0x00, 0, 1, 0, 0, 0, 0, 0, 0, 9]);
        query.extend(b"localhost");
        query.extend([0, 0, 1, 0, 1]);
        let frame = udp(DNS, 40000, 53, &query);
        self.exchange(&frame, |reply| {
            udp_from(reply, DNS, 53)
                .is_some_and(|data| data.get(0..2) == Some(&DNS_ID[..]) && data[2] & 0x80 != 0)
        })
    }

    /// Sends a SYN to `dst` on the port of a fresh listener on the host's loopback.
    pub fn walled_off(&mut self, dst: Ipv4Addr) -> Result<(), String> {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
            .and_then(|listener| listener.set_nonblocking(true).map(|()| listener))
            .map_err(|err| format!("a loopback listener: {err}"))?;
        let port = listener.local_addr().map_err(|err| err.to_string())?.port();
        self.send(&tcp_syn(dst, port))
            .map_err(|err| format!("sending to passt: {err}"))?;
        let deadline = Instant::now() + WAIT;
        while Instant::now() < deadline {
            if listener.accept().is_ok() {
                return Err(format!(
                    "a guest SYN to {dst}:{port} reached a listener on the host's 127.0.0.1"
                ));
            }
            sleep(POLL);
        }
        Ok(())
    }

    fn exchange(&mut self, frame: &[u8], answered: impl Fn(&[u8]) -> bool) -> bool {
        let deadline = Instant::now() + WAIT;
        while Instant::now() < deadline {
            if self.send(frame).is_err() {
                return false;
            }
            let round = (Instant::now() + RESEND).min(deadline);
            while let Some(reply) = self.receive(round) {
                if answered(&reply) {
                    return true;
                }
            }
        }
        false
    }

    fn send(&mut self, frame: &[u8]) -> std::io::Result<()> {
        let length = u32::try_from(frame.len()).unwrap_or(u32::MAX);
        self.stream.write_all(&length.to_be_bytes())?;
        self.stream.write_all(frame)
    }

    fn receive(&mut self, until: Instant) -> Option<Vec<u8>> {
        let left = until.checked_duration_since(Instant::now())?;
        self.stream
            .set_read_timeout(Some(left.max(Duration::from_millis(1))))
            .ok()?;
        let mut length = [0u8; 4];
        self.stream.read_exact(&mut length).ok()?;
        let length = usize::try_from(u32::from_be_bytes(length)).ok()?;
        if length > MAX_FRAME {
            return None;
        }
        let mut frame = vec![0u8; length];
        self.stream.read_exact(&mut frame).ok()?;
        Some(frame)
    }
}

fn checksum(chunks: &[&[u8]]) -> u16 {
    let mut sum = 0u32;
    for chunk in chunks {
        for pair in chunk.chunks(2) {
            sum += u32::from(u16::from_be_bytes([pair[0], *pair.get(1).unwrap_or(&0)]));
        }
    }
    while sum > 0xffff {
        sum = (sum & 0xffff) + (sum >> 16);
    }
    !u16::try_from(sum).unwrap_or(u16::MAX)
}

fn ethernet(dst: [u8; 6], ethertype: [u8; 2], payload: &[u8]) -> Vec<u8> {
    let mut frame = dst.to_vec();
    frame.extend(GUEST_MAC);
    frame.extend(ethertype);
    frame.extend(payload);
    frame
}

fn be16(value: usize) -> [u8; 2] {
    u16::try_from(value).unwrap_or(u16::MAX).to_be_bytes()
}

fn ipv4(protocol: u8, dst: Ipv4Addr, payload: &[u8]) -> Vec<u8> {
    let mut header = vec![0x45, 0];
    header.extend(be16(20 + payload.len()));
    header.extend([0, 1, 0x40, 0, 64, protocol, 0, 0]);
    header.extend(GUEST.octets());
    header.extend(dst.octets());
    let sum = checksum(&[&header]);
    header[10..12].copy_from_slice(&sum.to_be_bytes());
    header.extend(payload);
    ethernet(PASST_MAC, [0x08, 0x00], &header)
}

fn pseudo(protocol: u8, dst: Ipv4Addr, length: usize) -> Vec<u8> {
    let mut header = GUEST.octets().to_vec();
    header.extend(dst.octets());
    header.extend([0, protocol]);
    header.extend(be16(length));
    header
}

fn udp(dst: Ipv4Addr, src_port: u16, dst_port: u16, data: &[u8]) -> Vec<u8> {
    let mut segment = src_port.to_be_bytes().to_vec();
    segment.extend(dst_port.to_be_bytes());
    segment.extend(be16(8 + data.len()));
    segment.extend([0, 0]);
    segment.extend(data);
    let sum = checksum(&[&pseudo(17, dst, segment.len()), &segment]);
    segment[6..8].copy_from_slice(&sum.to_be_bytes());
    ipv4(17, dst, &segment)
}

fn tcp_syn(dst: Ipv4Addr, dst_port: u16) -> Vec<u8> {
    let mut segment = 41000u16.to_be_bytes().to_vec();
    segment.extend(dst_port.to_be_bytes());
    segment.extend(0x1000_0000u32.to_be_bytes());
    segment.extend(0u32.to_be_bytes());
    segment.extend([0x50, 0x02, 0xff, 0xff, 0, 0, 0, 0]);
    let sum = checksum(&[&pseudo(6, dst, segment.len()), &segment]);
    segment[16..18].copy_from_slice(&sum.to_be_bytes());
    ipv4(6, dst, &segment)
}

/// The UDP payload of a frame from `src`:`src_port`, if it is one.
fn udp_from(frame: &[u8], src: Ipv4Addr, src_port: u16) -> Option<&[u8]> {
    if frame.get(12..14) != Some(&[0x08, 0x00][..]) {
        return None;
    }
    let ip = frame.get(14..)?;
    let ihl = usize::from(ip.first()? & 0x0f) * 4;
    if ip.get(9) != Some(&17) || ip.get(12..16) != Some(&src.octets()[..]) {
        return None;
    }
    let segment = ip.get(ihl..)?;
    if segment.get(0..2) != Some(&src_port.to_be_bytes()[..]) {
        return None;
    }
    segment.get(8..)
}

#[cfg(test)]
mod tests;
