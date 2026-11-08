use std::fmt::Display;
use std::io::ErrorKind;
use std::net::Ipv4Addr;
use std::time::Duration;

use reqwest::redirect::Policy;
use reqwest::Client;
use tokio::io::AsyncReadExt;
use tokio::net::TcpStream;
use tokio::time::timeout;
use uuid::Uuid;

use crate::libs::api::{CheckStatus, DiskAssert, DiskResult, HttpProbe, NetworkProbe, Seed};

const PROBE_WAIT: Duration = Duration::from_secs(5);
// passt accepts on the guest's behalf, so a guest port that refuses shows up as a close just after.
const TCP_SETTLE: Duration = Duration::from_secs(2);
const MAX_BODY: usize = 64 * 1024;
pub(crate) const UNREADABLE: &str = "the disk could not be read";
const INSPECTOR: &str = include_str!("inspector.sh");
const ASSERTIONS: &str = "    : ASSERTIONS\n";

/// A probe and the agent host's loopback port forwarded to its guest port, when there is one.
#[derive(Debug, Clone)]
pub(crate) struct Aimed {
    pub host_port: Option<u16>,
    pub probe: NetworkProbe,
}

/// Asks every probe in turn; the first that fails says why, for the operator's eyes only.
pub(crate) async fn network(aimed: &[Aimed]) -> Result<(), String> {
    for item in aimed {
        let guest = item.probe.port;
        let Some(port) = item.host_port else {
            return Err(format!("port {guest}: not forwarded to this VM"));
        };
        let answer = match &item.probe.http {
            Some(http) => ask_http(port, http).await,
            None => ask_tcp(port).await,
        };
        answer.map_err(|why| format!("port {guest}: {why}"))?;
    }
    Ok(())
}

// The guest writes the answer, so it is read within bounds and never followed anywhere.
async fn ask_http(port: u16, http: &HttpProbe) -> Result<(), String> {
    let client = Client::builder()
        .no_proxy()
        .redirect(Policy::none())
        .timeout(PROBE_WAIT)
        .build()
        .map_err(|err| failed("probe-failed", port, &err.without_url()))?;
    let mut response = client
        .get(format!(
            "http://{}:{port}{}",
            Ipv4Addr::LOCALHOST,
            http.path
        ))
        .send()
        .await
        .map_err(|err| failed(http_code(&err), port, &err.without_url()))?;
    let status = response.status().as_u16();
    if status != http.status {
        return Err(format!("answered {status}, not {}", http.status));
    }
    let Some(wanted) = &http.contains else {
        return Ok(());
    };
    let mut body = Vec::new();
    while body.len() < MAX_BODY {
        let Some(chunk) = response
            .chunk()
            .await
            .map_err(|err| failed(http_code(&err), port, &err.without_url()))?
        else {
            break;
        };
        body.extend_from_slice(&chunk);
    }
    body.truncate(MAX_BODY);
    if holds(&body, wanted.as_bytes()) {
        Ok(())
    } else {
        Err("the answer lacks the expected text".into())
    }
}

async fn ask_tcp(port: u16) -> Result<(), String> {
    let mut stream = timeout(PROBE_WAIT, TcpStream::connect((Ipv4Addr::LOCALHOST, port)))
        .await
        .map_err(|err| failed("probe-timeout", port, &err))?
        .map_err(|err| {
            let code = match err.kind() {
                ErrorKind::ConnectionRefused => "probe-refused",
                _ => "probe-failed",
            };
            failed(code, port, &err)
        })?;
    let mut byte = [0_u8; 1];
    match timeout(TCP_SETTLE, stream.read(&mut byte)).await {
        Ok(Ok(0) | Err(_)) => Err("the guest closed the connection".into()),
        Ok(Ok(_)) | Err(_) => Ok(()),
    }
}

fn http_code(err: &reqwest::Error) -> &'static str {
    if err.is_timeout() {
        "probe-timeout"
    } else if err.is_connect() {
        "probe-refused"
    } else {
        "probe-failed"
    }
}

// The operator sees a fixed code and an error id; the library's own text goes to the log only.
fn failed(code: &str, port: u16, err: &dyn Display) -> String {
    let error_id = Uuid::new_v4();
    tracing::warn!(%error_id, code, port, error = %err, "a check from outside failed");
    format!("{code} (error {error_id})")
}

fn holds(body: &[u8], wanted: &[u8]) -> bool {
    !wanted.is_empty() && body.windows(wanted.len()).any(|window| window == wanted)
}

/// The seed of the inspector VM: a script of its own that reads `asserts` from the stopped VM's
/// disk, since root in the guest wrote that filesystem and no host kernel may mount it.
pub(crate) fn inspector_seed(vm_id: &str, asserts: &[DiskAssert]) -> Seed {
    let assertions: String = asserts.iter().map(assertion).collect();
    Seed {
        user_data: INSPECTOR.replace(ASSERTIONS, &assertions),
        meta_data: format!("instance-id: inspect-{vm_id}\n"),
    }
}

// A link does not count: an absolute one would resolve inside the inspector, not on the disk.
fn assertion(item: &DiskAssert) -> String {
    let path = quoted(&item.path);
    let search = item.contains.as_deref().map_or(String::new(), |text| {
        format!(
            "        grep -qF -- {} \"$root\"{path} && holds=1\n",
            quoted(text)
        )
    });
    format!(
        "    there=0\n    holds=0\n    \
         if [ -f \"$root\"{path} ] && [ ! -L \"$root\"{path} ]; then\n        \
         there=1\n{search}    fi\n    echo \"$there $holds\"\n"
    )
}

fn quoted(text: &str) -> String {
    format!("'{}'", text.replace('\'', r"'\''"))
}

/// What the inspector's `answers` come to; answers that do not fit `asserts` are an error.
pub(crate) fn judged(asserts: &[DiskAssert], answers: &[u8]) -> DiskResult {
    let Some(lines) = parsed(answers, asserts.len()) else {
        tracing::warn!("the inspector left no usable answers");
        return unreadable();
    };
    for (item, (there, holds)) in asserts.iter().zip(lines) {
        if item.exists != there {
            let state = if there { "is there" } else { "is missing" };
            let why = format!("{} {state}", item.path);
            return verdict(CheckStatus::Failed, Some(why));
        }
        if there && item.contains.is_some() && !holds {
            let why = format!("{} lacks the expected text", item.path);
            return verdict(CheckStatus::Failed, Some(why));
        }
    }
    verdict(CheckStatus::Passed, None)
}

fn parsed(answers: &[u8], count: usize) -> Option<Vec<(bool, bool)>> {
    let end = answers
        .iter()
        .position(|&byte| byte == 0)
        .unwrap_or(answers.len());
    let mut lines: Vec<&str> = std::str::from_utf8(&answers[..end]).ok()?.lines().collect();
    if lines.pop()? != "end" || lines.len() != count {
        return None;
    }
    lines
        .into_iter()
        .map(|line| match line {
            "0 0" => Some((false, false)),
            "1 0" => Some((true, false)),
            "1 1" => Some((true, true)),
            _ => None,
        })
        .collect()
}

pub(crate) fn unreadable() -> DiskResult {
    verdict(CheckStatus::Error, Some(UNREADABLE.into()))
}

pub(crate) fn verdict(status: CheckStatus, message: Option<String>) -> DiskResult {
    DiskResult { status, message }
}

#[cfg(test)]
mod tests;
