use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::unix::{OwnedReadHalf, OwnedWriteHalf};
use tokio::net::UnixStream;
use tokio::time::{sleep, timeout_at, Instant};

use crate::libs::error::AgentError;

const MAX_MESSAGE: u64 = 1 << 20;
const DELIMITER: u8 = 0xFF;
const SYNC: &str = "guest-sync-delimited";
const CHUNK: usize = 64 * 1024;
const MISSING: &str = "No such file or directory";
const EXEC_POLL: Duration = Duration::from_millis(100);

pub struct Qga {
    reader: BufReader<OwnedReadHalf>,
    writer: OwnedWriteHalf,
    timeout: Duration,
}

impl Qga {
    pub async fn connect(path: &Path, timeout: Duration) -> Result<Self, AgentError> {
        let deadline = Instant::now() + timeout;
        let stream = timeout_at(deadline, UnixStream::connect(path))
            .await
            .map_err(|_| AgentError::Qga("connect timed out".into()))?
            .map_err(|err| AgentError::Qga(format!("connect: {err}")))?;
        let (reader, writer) = stream.into_split();
        let mut qga = Self {
            reader: BufReader::new(reader),
            writer,
            timeout,
        };
        qga.sync(deadline).await?;
        Ok(qga)
    }

    pub async fn execute(
        &mut self,
        command: &str,
        args: Option<Value>,
    ) -> Result<Value, AgentError> {
        let reply = self.request(command, args).await?;
        returned(command, reply)
    }

    pub async fn ping(&mut self) -> Result<(), AgentError> {
        self.execute("guest-ping", None).await.map(drop)
    }

    /// Runs `argv` in the guest and returns its exit code; an error once `wait` passes.
    pub async fn exec(&mut self, argv: &[&str], wait: Duration) -> Result<i64, AgentError> {
        let command = "guest-exec";
        let (path, args) = argv
            .split_first()
            .ok_or_else(|| AgentError::Qga(format!("{command}: no command")))?;
        let deadline = Instant::now() + wait;
        let started = self
            .execute(
                command,
                Some(json!({ "path": path, "arg": args, "capture-output": false })),
            )
            .await?;
        let pid = started
            .get("pid")
            .and_then(Value::as_i64)
            .ok_or_else(|| AgentError::Qga(format!("{command}: no pid")))?;
        let command = "guest-exec-status";
        loop {
            let status = self.execute(command, Some(json!({ "pid": pid }))).await?;
            if status.get("exited").and_then(Value::as_bool) == Some(true) {
                return status
                    .get("exitcode")
                    .and_then(Value::as_i64)
                    .ok_or_else(|| AgentError::Qga(format!("{command}: no exit code")));
            }
            if Instant::now() + EXEC_POLL > deadline {
                return Err(AgentError::Qga(format!("{path}: still running")));
            }
            sleep(EXEC_POLL).await;
        }
    }

    pub async fn read_file(
        &mut self,
        path: &str,
        max: usize,
    ) -> Result<Option<Vec<u8>>, AgentError> {
        let open = "guest-file-open";
        let reply = self
            .request(open, Some(json!({ "path": path, "mode": "r" })))
            .await?;
        if desc(&reply).is_some_and(|text| text.contains(MISSING)) {
            return Ok(None);
        }
        let handle = returned(open, reply)?
            .as_i64()
            .ok_or_else(|| AgentError::Qga(format!("{open}: no handle")))?;
        let read = self.read_open(path, handle, max).await;
        let closed = self
            .execute("guest-file-close", Some(json!({ "handle": handle })))
            .await;
        let data = read?;
        closed?;
        Ok(Some(data))
    }

    async fn read_open(
        &mut self,
        path: &str,
        handle: i64,
        max: usize,
    ) -> Result<Vec<u8>, AgentError> {
        let command = "guest-file-read";
        let mut data = Vec::new();
        loop {
            let reply = self
                .execute(command, Some(json!({ "handle": handle, "count": CHUNK })))
                .await?;
            let chunk = reply
                .get("buf-b64")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let chunk = STANDARD
                .decode(chunk)
                .map_err(|err| AgentError::Qga(format!("{command}: {err}")))?;
            if data.len() + chunk.len() > max {
                return Err(AgentError::Qga(format!("{path}: over {max} bytes")));
            }
            let eof = reply.get("eof").and_then(Value::as_bool).unwrap_or(false);
            if chunk.is_empty() && !eof {
                return Err(AgentError::Qga(format!("{command}: no data before eof")));
            }
            data.extend(chunk);
            if eof {
                return Ok(data);
            }
        }
    }

    async fn sync(&mut self, deadline: Instant) -> Result<(), AgentError> {
        let id = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as u64;
        let mut message = vec![DELIMITER];
        message.extend(
            json!({ "execute": SYNC, "arguments": { "id": id } })
                .to_string()
                .bytes(),
        );
        message.push(b'\n');
        self.send(SYNC, &message, deadline).await?;
        loop {
            let line = self.read_line(SYNC, deadline).await?;
            // The guest puts 0xFF right before its sync reply; anything up to it is stale.
            let Some(at) = line.iter().rposition(|&byte| byte == DELIMITER) else {
                continue;
            };
            let reply = serde_json::from_slice::<Value>(&line[at + 1..]).ok();
            if reply.and_then(|reply| reply.get("return").and_then(Value::as_u64)) == Some(id) {
                return Ok(());
            }
        }
    }

    async fn request(&mut self, command: &str, args: Option<Value>) -> Result<Value, AgentError> {
        let mut message = json!({ "execute": command });
        if let Some(args) = args {
            message["arguments"] = args;
        }
        let mut line = message.to_string().into_bytes();
        line.push(b'\n');
        let deadline = Instant::now() + self.timeout;
        self.send(command, &line, deadline).await?;
        let line = self.read_line(command, deadline).await?;
        serde_json::from_slice(&line)
            .map_err(|err| AgentError::Qga(format!("{command}: bad message: {err}")))
    }

    async fn send(
        &mut self,
        command: &str,
        bytes: &[u8],
        deadline: Instant,
    ) -> Result<(), AgentError> {
        timeout_at(deadline, self.writer.write_all(bytes))
            .await
            .map_err(|_| AgentError::Qga(format!("{command}: timed out")))?
            .map_err(|err| AgentError::Qga(format!("{command}: {err}")))
    }

    async fn read_line(&mut self, command: &str, deadline: Instant) -> Result<Vec<u8>, AgentError> {
        let mut line = Vec::new();
        let mut limited = (&mut self.reader).take(MAX_MESSAGE);
        let size = timeout_at(deadline, limited.read_until(b'\n', &mut line))
            .await
            .map_err(|_| AgentError::Qga(format!("{command}: timed out")))?
            .map_err(|err| AgentError::Qga(format!("{command}: {err}")))?;
        if size == 0 {
            return Err(AgentError::Qga(format!("{command}: connection closed")));
        }
        if line.last() != Some(&b'\n') {
            return Err(AgentError::Qga(format!("{command}: truncated message")));
        }
        Ok(line)
    }
}

fn returned(command: &str, mut reply: Value) -> Result<Value, AgentError> {
    if let Some(value) = reply.get_mut("return") {
        return Ok(value.take());
    }
    let desc = desc(&reply).unwrap_or("unexpected reply");
    Err(AgentError::Qga(format!("{command}: {desc}")))
}

fn desc(reply: &Value) -> Option<&str> {
    reply.pointer("/error/desc").and_then(Value::as_str)
}

#[cfg(test)]
mod tests;
