use std::fs::File;
use std::io::{self, ErrorKind, Read, Seek, SeekFrom};
use std::path::Path;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use tokio::time::{interval, timeout, MissedTickBehavior};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::handshake::client::Request;
use tokio_tungstenite::tungstenite::http::HeaderValue;
use tokio_tungstenite::tungstenite::Message;
use url::Url;

use crate::libs::agent::{Auth, Signer};
use crate::libs::api::{sign, unix_now};
use crate::libs::error::AgentError;

const CONNECT_WAIT: Duration = Duration::from_secs(10);
const POLL: Duration = Duration::from_millis(50);
const CHUNK: usize = 64 * 1024;
const FULL: u16 = 4413;

#[derive(Deserialize)]
struct Reply {
    offset: Option<u64>,
}

fn failed(err: impl std::fmt::Display) -> AgentError {
    AgentError::Api(format!("console socket: {err}"))
}

/// Reads from `offset`; 0 at or past the end and for a missing file.
pub fn read_at(path: &Path, offset: u64, buf: &mut [u8]) -> io::Result<usize> {
    let mut file = match File::open(path) {
        Err(err) if err.kind() == ErrorKind::NotFound => return Ok(0),
        other => other?,
    };
    file.seek(SeekFrom::Start(offset))?;
    file.read(buf)
}

fn request(url: &Url, signer: &Signer, deployment_id: &str) -> Result<Request, AgentError> {
    let target = url
        .join(&format!(
            "api/v1/agents/{}/deployments/{deployment_id}/console",
            signer.agent_id
        ))
        .map_err(failed)?;
    // http becomes ws and https becomes wss.
    let rest = target
        .as_str()
        .strip_prefix("http")
        .ok_or_else(|| failed("the API URL is not http or https"))?;
    let timestamp = unix_now().to_string();
    let signature = sign(
        &signer.credential.key,
        &timestamp,
        "GET",
        target.path(),
        &[],
    )?;
    let mut request = format!("ws{rest}").into_client_request().map_err(failed)?;
    for (name, value) in [
        ("X-Credential", signer.credential.credential.as_str()),
        ("X-Timestamp", timestamp.as_str()),
        ("X-Signature", signature.as_str()),
    ] {
        let value = HeaderValue::from_str(value).map_err(|_| failed("a header is not ASCII"))?;
        request.headers_mut().insert(name, value);
    }
    Ok(request)
}

/// Ships `log` by offset until the socket ends; `true` when the API closed it with 4413.
pub async fn ship(
    url: &Url,
    auth: &Auth,
    deployment_id: &str,
    log: &Path,
) -> Result<bool, AgentError> {
    // The credential is held for the handshake only, never for the socket's lifetime.
    let connect = async {
        let signer = auth.read().await;
        let signer = signer.as_ref().ok_or_else(|| failed("no credential"))?;
        let request = request(url, signer, deployment_id)?;
        tokio_tungstenite::connect_async(request)
            .await
            .map_err(failed)
    };
    let (mut socket, _) = timeout(CONNECT_WAIT, connect)
        .await
        .map_err(|_| failed("connecting timed out"))??;

    let mut offset = 0;
    // Nothing is sent before the API says how far it holds the log.
    let mut unanswered = true;
    let mut buffer = vec![0; CHUNK];
    let mut ticker = interval(POLL);
    ticker.set_missed_tick_behavior(MissedTickBehavior::Delay);
    loop {
        tokio::select! {
            message = socket.next() => {
                let Some(message) = message else {
                    return Ok(false);
                };
                match message.map_err(failed)? {
                    Message::Text(text) => {
                        let reply: Reply = serde_json::from_str(&text)
                            .map_err(|_| failed("an answer that is not JSON"))?;
                        if let Some(held) = reply.offset {
                            offset = held;
                            unanswered = false;
                        }
                    }
                    Message::Close(frame) => {
                        return Ok(frame.is_some_and(|frame| u16::from(frame.code) == FULL));
                    }
                    _ => {}
                }
            }
            _ = ticker.tick(), if !unanswered => {
                let read = read_at(log, offset, &mut buffer)?;
                if read > 0 {
                    let mut frame = offset.to_be_bytes().to_vec();
                    frame.extend_from_slice(&buffer[..read]);
                    socket.send(Message::Binary(frame)).await.map_err(failed)?;
                    unanswered = true;
                }
            }
        }
    }
}

#[cfg(test)]
mod tests;
