use std::fs::File;
use std::io::{self, ErrorKind, Read, Seek, SeekFrom};
use std::ops::RangeInclusive;
use std::path::Path;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use serde_json::Value;
use tokio::io::AsyncWriteExt;
use tokio::net::unix::OwnedWriteHalf;
use tokio::net::UnixStream;
use tokio::task::JoinHandle;
use tokio::time::{interval, timeout, MissedTickBehavior};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::handshake::client::Request;
use tokio_tungstenite::tungstenite::http::HeaderValue;
use tokio_tungstenite::tungstenite::Message;
use url::Url;

use crate::libs::agent::{Auth, Signer};
use crate::libs::api::{sign, unix_now};
use crate::libs::error::AgentError;
use crate::libs::qga::Qga;
use crate::libs::vm;

const CONNECT_WAIT: Duration = Duration::from_secs(10);
const POLL: Duration = Duration::from_millis(50);
const CHUNK: usize = 64 * 1024;
const FULL: u16 = 4413;
const KEYS_WAIT: Duration = Duration::from_secs(1);
const RESIZE_WAIT: Duration = Duration::from_secs(5);
const SIDE: RangeInclusive<u64> = 1..=500;
const TTY: &str = "/dev/ttyS0";

#[derive(Deserialize)]
struct Reply {
    offset: Option<u64>,
    cols: Option<Value>,
    rows: Option<Value>,
}

fn side(value: Option<Value>) -> Option<u64> {
    value?.as_u64().filter(|side| SIDE.contains(side))
}

/// `serial.sock` held for typing. What the guest prints there is drained:
/// left unread it fills the socket and stalls the guest's serial output.
struct Keyboard {
    keys: OwnedWriteHalf,
    drain: JoinHandle<()>,
}

impl Keyboard {
    async fn open(path: &Path) -> io::Result<Self> {
        let (mut echo, keys) = UnixStream::connect(path).await?.into_split();
        let drain = tokio::spawn(async move {
            let _ = tokio::io::copy(&mut echo, &mut tokio::io::sink()).await;
        });
        Ok(Self { keys, drain })
    }
}

impl Drop for Keyboard {
    fn drop(&mut self) {
        self.drain.abort();
    }
}

struct Guest<'a> {
    deployment_id: &'a str,
    dir: &'a Path,
    keyboard: Option<Keyboard>,
    size: Option<(u64, u64)>,
}

impl Guest<'_> {
    async fn type_in(&mut self, keys: &[u8]) {
        let held = self.keyboard.take();
        let serial = self.dir.join(vm::SERIAL);
        // A keyboard that fails or times out is dropped here, so the next key connects again.
        let typing = async {
            let mut keyboard = match held {
                Some(keyboard) => keyboard,
                None => Keyboard::open(&serial).await?,
            };
            keyboard.keys.write_all(keys).await?;
            Ok::<_, io::Error>(keyboard)
        };
        let typed = timeout(KEYS_WAIT, typing)
            .await
            .unwrap_or_else(|_| Err(ErrorKind::TimedOut.into()));
        match typed {
            Ok(keyboard) => self.keyboard = Some(keyboard),
            Err(err) => tracing::warn!(
                deployment_id = self.deployment_id,
                error = %err,
                "the keys did not reach the guest"
            ),
        }
    }

    async fn resize(&mut self, cols: u64, rows: u64) {
        if self.size == Some((cols, rows)) {
            return;
        }
        let deployment_id = self.deployment_id;
        let socket = self.dir.join(vm::QGA);
        let (wide, high) = (cols.to_string(), rows.to_string());
        let argv = [
            "stty",
            "-F",
            TTY,
            "cols",
            wide.as_str(),
            "rows",
            high.as_str(),
        ];
        let run = async {
            let mut qga = Qga::connect(&socket, RESIZE_WAIT).await?;
            qga.exec(&argv, RESIZE_WAIT).await
        };
        match timeout(RESIZE_WAIT, run).await {
            Ok(Ok(0)) => {
                self.size = Some((cols, rows));
                tracing::info!(deployment_id, cols, rows, "the guest took the size");
            }
            Ok(Ok(code)) => tracing::warn!(deployment_id, code, "stty refused the size"),
            Ok(Err(err)) => tracing::warn!(deployment_id, error = %err, "the resize failed"),
            Err(_) => tracing::warn!(deployment_id, "the resize timed out"),
        }
    }
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

/// Ships the console log of the VM in `dir` by offset and passes keys and resizes
/// to its guest until the socket ends; `true` when the API closed it with 4413.
pub async fn ship(
    url: &Url,
    auth: &Auth,
    deployment_id: &str,
    dir: &Path,
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

    let log = dir.join(vm::CONSOLE_LOG);
    let mut guest = Guest {
        deployment_id,
        dir,
        keyboard: None,
        size: None,
    };
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
                        if let (Some(cols), Some(rows)) = (side(reply.cols), side(reply.rows)) {
                            guest.resize(cols, rows).await;
                        }
                    }
                    Message::Binary(keys) if !keys.is_empty() => guest.type_in(&keys).await,
                    Message::Close(frame) => {
                        return Ok(frame.is_some_and(|frame| u16::from(frame.code) == FULL));
                    }
                    _ => {}
                }
            }
            _ = ticker.tick(), if !unanswered => {
                let read = read_at(&log, offset, &mut buffer)?;
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
