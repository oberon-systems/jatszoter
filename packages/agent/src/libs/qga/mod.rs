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
const STATUS: &str = "guest-exec-status";
const CHUNK: usize = 64 * 1024;
const MISSING: &str = "No such file or directory";
const EXEC_POLL: Duration = Duration::from_millis(100);
/// How the error of a reply over the message limit ends.
pub const TRUNCATED: &str = "truncated message";

/// How a captured run ended; the outputs are a script's, so `Debug` prints no content.
#[derive(PartialEq, Eq)]
pub enum Ran {
    Exited {
        code: i64,
        stdout: Vec<u8>,
        stderr: Vec<u8>,
    },
    Overdue {
        pid: i64,
    },
}

impl std::fmt::Debug for Ran {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Exited { code, .. } => f
                .debug_struct("Exited")
                .field("code", code)
                .finish_non_exhaustive(),
            Self::Overdue { pid } => f.debug_struct("Overdue").field("pid", pid).finish(),
        }
    }
}

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
        let deadline = Instant::now() + wait;
        let pid = self.spawn(argv, false).await?;
        loop {
            if let Some(status) = self.exited(pid).await? {
                return exit_code(&status);
            }
            if Instant::now() + EXEC_POLL > deadline {
                let path = argv.first().copied().unwrap_or_default();
                return Err(AgentError::Qga(format!("{path}: still running")));
            }
            sleep(EXEC_POLL).await;
        }
    }

    /// Runs `argv` with captured output; once `wait` passes it answers the pid, still running.
    pub async fn run(&mut self, argv: &[&str], wait: Duration) -> Result<Ran, AgentError> {
        let deadline = Instant::now() + wait;
        let pid = self.spawn(argv, true).await?;
        loop {
            if let Some(status) = self.exited(pid).await? {
                return Ok(Ran::Exited {
                    code: exit_code(&status)?,
                    stdout: output(&status, "out-data")?,
                    stderr: output(&status, "err-data")?,
                });
            }
            if Instant::now() + EXEC_POLL > deadline {
                return Ok(Ran::Overdue { pid });
            }
            sleep(EXEC_POLL).await;
        }
    }

    async fn spawn(&mut self, argv: &[&str], capture: bool) -> Result<i64, AgentError> {
        let command = "guest-exec";
        let (path, args) = argv
            .split_first()
            .ok_or_else(|| AgentError::Qga(format!("{command}: no command")))?;
        self.execute(
            command,
            Some(json!({ "path": path, "arg": args, "capture-output": capture })),
        )
        .await?
        .get("pid")
        .and_then(Value::as_i64)
        .ok_or_else(|| AgentError::Qga(format!("{command}: no pid")))
    }

    async fn exited(&mut self, pid: i64) -> Result<Option<Value>, AgentError> {
        let status = self.execute(STATUS, Some(json!({ "pid": pid }))).await?;
        let exited = status.get("exited").and_then(Value::as_bool) == Some(true);
        Ok(exited.then_some(status))
    }

    pub async fn write_file(&mut self, path: &str, data: &[u8]) -> Result<(), AgentError> {
        let open = "guest-file-open";
        let handle = self
            .execute(open, Some(json!({ "path": path, "mode": "w" })))
            .await?
            .as_i64()
            .ok_or_else(|| AgentError::Qga(format!("{open}: no handle")))?;
        let written = self.write_open(handle, data).await;
        let closed = self
            .execute("guest-file-close", Some(json!({ "handle": handle })))
            .await;
        written?;
        closed.map(drop)
    }

    async fn write_open(&mut self, handle: i64, data: &[u8]) -> Result<(), AgentError> {
        let command = "guest-file-write";
        for chunk in data.chunks(CHUNK) {
            let reply = self
                .execute(
                    command,
                    Some(json!({ "handle": handle, "buf-b64": STANDARD.encode(chunk) })),
                )
                .await?;
            let count = reply.get("count").and_then(Value::as_u64);
            if count != u64::try_from(chunk.len()).ok() {
                return Err(AgentError::Qga(format!("{command}: short write")));
            }
        }
        Ok(())
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
            return Err(AgentError::Qga(format!("{command}: {TRUNCATED}")));
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

fn exit_code(status: &Value) -> Result<i64, AgentError> {
    status
        .get("exitcode")
        .and_then(Value::as_i64)
        .ok_or_else(|| AgentError::Qga(format!("{STATUS}: no exit code")))
}

fn output(status: &Value, field: &str) -> Result<Vec<u8>, AgentError> {
    let data = status
        .get(field)
        .and_then(Value::as_str)
        .unwrap_or_default();
    STANDARD
        .decode(data)
        .map_err(|err| AgentError::Qga(format!("{STATUS}: {field}: {err}")))
}

fn desc(reply: &Value) -> Option<&str> {
    reply.pointer("/error/desc").and_then(Value::as_str)
}

#[cfg(test)]
mod tests;
