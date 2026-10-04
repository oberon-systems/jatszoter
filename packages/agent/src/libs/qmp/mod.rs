use std::path::Path;
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::unix::{OwnedReadHalf, OwnedWriteHalf};
use tokio::net::UnixStream;
use tokio::time::{timeout_at, Instant};

use crate::libs::error::AgentError;

const MAX_MESSAGE: u64 = 1 << 20;

pub struct Qmp {
    reader: BufReader<OwnedReadHalf>,
    writer: OwnedWriteHalf,
    timeout: Duration,
}

impl Qmp {
    pub async fn connect(path: &Path, timeout: Duration) -> Result<Self, AgentError> {
        let deadline = Instant::now() + timeout;
        let stream = timeout_at(deadline, UnixStream::connect(path))
            .await
            .map_err(|_| AgentError::Qmp("connect timed out".into()))?
            .map_err(|err| AgentError::Qmp(format!("connect: {err}")))?;
        let (reader, writer) = stream.into_split();
        let mut qmp = Self {
            reader: BufReader::new(reader),
            writer,
            timeout,
        };
        if qmp.read(deadline).await?.get("QMP").is_none() {
            return Err(AgentError::Qmp("no greeting".into()));
        }
        qmp.execute("qmp_capabilities", None).await?;
        Ok(qmp)
    }

    pub async fn execute(
        &mut self,
        command: &str,
        args: Option<Value>,
    ) -> Result<Value, AgentError> {
        let mut message = json!({ "execute": command });
        if let Some(args) = args {
            message["arguments"] = args;
        }
        let mut line = message.to_string().into_bytes();
        line.push(b'\n');
        let deadline = Instant::now() + self.timeout;
        timeout_at(deadline, self.writer.write_all(&line))
            .await
            .map_err(|_| AgentError::Qmp(format!("{command}: timed out")))?
            .map_err(|err| AgentError::Qmp(format!("{command}: {err}")))?;
        loop {
            let mut reply = self.read(deadline).await?;
            if reply.get("event").is_some() {
                continue;
            }
            if let Some(value) = reply.get_mut("return") {
                return Ok(value.take());
            }
            let desc = reply
                .pointer("/error/desc")
                .and_then(Value::as_str)
                .unwrap_or("unexpected reply");
            return Err(AgentError::Qmp(format!("{command}: {desc}")));
        }
    }

    async fn read(&mut self, deadline: Instant) -> Result<Value, AgentError> {
        let mut line = String::new();
        let mut limited = (&mut self.reader).take(MAX_MESSAGE);
        let size = timeout_at(deadline, limited.read_line(&mut line))
            .await
            .map_err(|_| AgentError::Qmp("timed out".into()))?
            .map_err(|err| AgentError::Qmp(err.to_string()))?;
        if size == 0 {
            return Err(AgentError::Qmp("connection closed".into()));
        }
        if !line.ends_with('\n') {
            return Err(AgentError::Qmp("truncated message".into()));
        }
        serde_json::from_str(&line).map_err(|err| AgentError::Qmp(format!("bad message: {err}")))
    }
}

#[cfg(test)]
mod tests;
