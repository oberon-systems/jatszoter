use std::path::Path;
use std::time::Duration;

use tokio::time::{sleep, timeout};

use crate::libs::agent::Auth;
use crate::libs::api::{Api, CheckResult, CheckStatus, Posted, Script, Signed};
use crate::libs::error::AgentError;
use crate::libs::qga::{Qga, Ran, TRUNCATED};
use crate::libs::vm;

// Bounds how long a request holds the credential, so a refresh is never held off.
const SIGN_WAIT: Duration = Duration::from_secs(15);
const QGA_WAIT: Duration = Duration::from_secs(5);
const ATTEMPTS: u32 = 3;
const RETRY: Duration = Duration::from_secs(2);
const MAX_MESSAGE: usize = 200;
const MAX_DETAIL: usize = 4096;
const SILENT: &str = "the guest agent does not answer";
const UNWRITTEN: &str = "the check script could not be written";
const OVERSIZE: &str = "the check output is too large to read";
const BROKEN: &str = "the guest agent failed to run the check";
const RANGE: &str = "the exit code is out of range";

/// Runs one pending check in the guest behind `socket` and posts what it came to.
pub(crate) async fn run(
    api: &impl Api,
    auth: &Auth,
    socket: &Path,
    deployment_id: &str,
    check_id: &str,
) {
    if !vm::valid_id(check_id) {
        tracing::warn!("the check id is not a valid name");
        return;
    }
    let Some(script) = fetch(api, auth, deployment_id, check_id).await else {
        return;
    };
    let path = format!("/run/jatszoter-check-{check_id}.sh");
    let result = match Qga::connect(socket, QGA_WAIT).await {
        Ok(mut qga) => {
            let (result, usable) = execute(&mut qga, &path, &script).await;
            // A failed exchange may leave a reply unread, so the delete takes a fresh connection;
            // the guest agent socket serves one client, so the old one is closed first.
            let removed = if usable {
                remove(&mut qga, &path).await
            } else {
                drop(qga);
                match Qga::connect(socket, QGA_WAIT).await {
                    Ok(mut fresh) => remove(&mut fresh, &path).await,
                    Err(err) => Err(err),
                }
            };
            if let Err(err) = removed {
                tracing::warn!(%check_id, error = %err, "deleting the check script failed");
            }
            result
        }
        Err(err) => {
            tracing::warn!(error = %err, "the guest agent does not answer");
            failure(SILENT)
        }
    };
    post(api, auth, deployment_id, check_id, &result).await;
}

async fn execute(qga: &mut Qga, path: &str, script: &Script) -> (CheckResult, bool) {
    if let Err(err) = qga.write_file(path, script.script.as_bytes()).await {
        tracing::warn!(error = %err, "writing the check script failed");
        return (failure(UNWRITTEN), false);
    }
    let wait = Duration::from_secs(script.timeout_seconds);
    match qga.run(&["/bin/sh", path], wait).await {
        Ok(Ran::Exited {
            code,
            stdout,
            stderr,
        }) => (verdict(code, &stdout, &stderr), true),
        Ok(Ran::Overdue { pid }) => {
            let killed = qga
                .exec(&["/bin/kill", "-KILL", &pid.to_string()], QGA_WAIT)
                .await;
            if let Err(err) = &killed {
                tracing::warn!(error = %err, "killing the check failed");
            }
            let result = CheckResult {
                status: CheckStatus::Timeout,
                exit_code: None,
                message: None,
                detail: None,
            };
            (result, killed.is_ok())
        }
        Err(err) => {
            tracing::warn!(error = %err, "running the check failed");
            let oversize = matches!(&err, AgentError::Qga(text) if text.ends_with(TRUNCATED));
            (failure(if oversize { OVERSIZE } else { BROKEN }), false)
        }
    }
}

async fn remove(qga: &mut Qga, path: &str) -> Result<(), AgentError> {
    match qga.exec(&["/bin/rm", "-f", "--", path], QGA_WAIT).await? {
        0 => Ok(()),
        code => Err(AgentError::Qga(format!("rm exited {code}"))),
    }
}

fn failure(message: &str) -> CheckResult {
    CheckResult {
        status: CheckStatus::Error,
        exit_code: None,
        message: Some(message.into()),
        detail: None,
    }
}

fn verdict(code: i64, stdout: &[u8], stderr: &[u8]) -> CheckResult {
    let Ok(code) = i32::try_from(code) else {
        return failure(RANGE);
    };
    CheckResult {
        status: if code == 0 {
            CheckStatus::Passed
        } else {
            CheckStatus::Failed
        },
        exit_code: Some(code),
        message: message(stdout),
        detail: detail(stderr),
    }
}

fn message(stdout: &[u8]) -> Option<String> {
    let line = stdout
        .split(|&byte| byte == b'\n')
        .next()
        .unwrap_or_default();
    let text: String = String::from_utf8_lossy(line)
        .trim_end_matches('\r')
        .chars()
        .filter(|&letter| letter != '\0')
        .take(MAX_MESSAGE)
        .collect();
    (!text.is_empty()).then_some(text)
}

fn detail(stderr: &[u8]) -> Option<String> {
    let head = &stderr[..stderr.len().min(MAX_DETAIL)];
    let mut text = String::from_utf8_lossy(head).replace('\0', "");
    // A character cut in half decodes into a longer replacement.
    while text.len() > MAX_DETAIL {
        text.pop();
    }
    (!text.is_empty()).then_some(text)
}

async fn fetch(api: &impl Api, auth: &Auth, deployment_id: &str, check_id: &str) -> Option<Script> {
    for attempt in 1..=ATTEMPTS {
        let call = async {
            let signer = auth.read().await;
            let Some(signer) = signer.as_ref() else {
                return Ok(Signed::Refused);
            };
            api.script(
                &signer.agent_id,
                &signer.credential,
                deployment_id,
                check_id,
            )
            .await
        };
        match timeout(SIGN_WAIT, call).await {
            Ok(Ok(Signed::Accepted(Some(script)))) => return Some(script),
            Ok(Ok(Signed::Accepted(None))) => {
                tracing::debug!("the check is no longer pending");
                return None;
            }
            Ok(Ok(Signed::Refused)) => {
                tracing::warn!("the API refused the credential");
                return None;
            }
            Ok(Err(err)) => tracing::warn!(error = %err, attempt, "fetching the check failed"),
            Err(_) => tracing::warn!(attempt, "fetching the check timed out"),
        }
        if attempt < ATTEMPTS {
            sleep(RETRY).await;
        }
    }
    None
}

async fn post(
    api: &impl Api,
    auth: &Auth,
    deployment_id: &str,
    check_id: &str,
    result: &CheckResult,
) {
    for attempt in 1..=ATTEMPTS {
        let call = async {
            let signer = auth.read().await;
            let Some(signer) = signer.as_ref() else {
                return Ok(Signed::Refused);
            };
            api.result(
                &signer.agent_id,
                &signer.credential,
                deployment_id,
                check_id,
                result,
            )
            .await
        };
        match timeout(SIGN_WAIT, call).await {
            Ok(Ok(Signed::Accepted(Posted::Stored))) => {
                tracing::info!(status = ?result.status, "the check result is posted");
                return;
            }
            Ok(Ok(Signed::Accepted(Posted::Conflict))) => {
                tracing::info!("the check is no longer pending; the result is dropped");
                return;
            }
            Ok(Ok(Signed::Refused)) => {
                tracing::warn!("the API refused the credential");
                return;
            }
            Ok(Err(err)) => tracing::warn!(error = %err, attempt, "posting the result failed"),
            Err(_) => tracing::warn!(attempt, "posting the result timed out"),
        }
        if attempt < ATTEMPTS {
            sleep(RETRY).await;
        }
    }
}

#[cfg(test)]
mod tests;
