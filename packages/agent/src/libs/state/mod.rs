use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::libs::error::AgentError;

const PRIVATE_MODE: u32 = 0o600;
const MAX_STATE_FILE: u64 = 64 * 1024;

/// What the agent signs with: `credential` goes verbatim into `X-Credential`.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Credential {
    pub credential: String,
    pub expires_at: i64,
    pub key: String,
}

impl fmt::Debug for Credential {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Credential")
            .field("credential", &self.credential)
            .field("expires_at", &self.expires_at)
            .field("key", &"<redacted>")
            .finish()
    }
}

/// Everything the agent keeps across restarts; the id survives a new registration.
#[derive(Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct State {
    pub agent_id: Option<String>,
    pub claim_code: Option<String>,
    pub credential: Option<Credential>,
}

impl fmt::Debug for State {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("State")
            .field("agent_id", &self.agent_id)
            .field(
                "claim_code",
                &self.claim_code.as_ref().map(|_| "<redacted>"),
            )
            .field("credential", &self.credential)
            .finish()
    }
}

#[derive(Debug, Clone)]
pub struct StateStore {
    path: PathBuf,
}

impl StateStore {
    pub fn new(state_dir: &Path) -> Self {
        Self {
            path: state_dir.join("state.json"),
        }
    }

    pub fn load(&self) -> Result<State, AgentError> {
        let raw = match read_private(&self.path) {
            Ok(raw) => raw,
            Err(AgentError::Io(err)) if err.kind() == ErrorKind::NotFound => {
                return Ok(State::default())
            }
            Err(err) => return Err(err),
        };
        serde_json::from_str(&raw)
            .map_err(|_| AgentError::State(format!("{} is malformed", self.path.display())))
    }

    pub fn save(&self, state: &State) -> Result<(), AgentError> {
        let body = serde_json::to_vec(state).map_err(|err| AgentError::State(err.to_string()))?;
        let tmp = self.path.with_extension("json.tmp");
        match fs::remove_file(&tmp) {
            Err(err) if err.kind() != ErrorKind::NotFound => return Err(err.into()),
            _ => {}
        }
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(PRIVATE_MODE)
            .open(&tmp)?;
        file.write_all(&body)?;
        file.sync_all()?;
        fs::rename(&tmp, &self.path)?;
        Ok(())
    }
}

fn read_private(path: &Path) -> Result<String, AgentError> {
    let reject = |why: &str| AgentError::State(format!("{} {why}", path.display()));
    if fs::symlink_metadata(path)?.file_type().is_symlink() {
        return Err(reject("must not be a symlink"));
    }
    let file = File::open(path)?;
    let meta = file.metadata()?;
    if !meta.is_file() {
        return Err(reject("must be a regular file"));
    }
    if meta.permissions().mode() & 0o077 != 0 {
        return Err(reject("must be private to its owner"));
    }
    if meta.len() > MAX_STATE_FILE {
        return Err(reject("is too large"));
    }
    let mut raw = String::new();
    file.take(MAX_STATE_FILE).read_to_string(&mut raw)?;
    Ok(raw)
}

#[cfg(test)]
mod tests;
