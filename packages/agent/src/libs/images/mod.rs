use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use reqwest::{redirect, Client};
use sha2::{Digest, Sha256};
use tokio::io::AsyncWriteExt;
use url::Url;

use crate::libs::config::{is_https_or_loopback, prepare_private_dir};
use crate::libs::error::AgentError;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const READ_TIMEOUT: Duration = Duration::from_secs(60);
const MAX_REDIRECTS: usize = 10;
const PART: &str = ".part-";

#[allow(dead_code, reason = "driven by the reconcile loop, prompt 10")]
pub struct ImageCache {
    dir: PathBuf,
    limit: u64,
    client: Client,
    locks: Mutex<HashMap<String, Arc<tokio::sync::Mutex<()>>>>,
}

#[allow(dead_code, reason = "driven by the reconcile loop, prompt 10")]
impl ImageCache {
    pub fn new(dir: &Path, limit_bytes: u64) -> Result<Self, AgentError> {
        prepare_private_dir(dir)?;
        for entry in fs::read_dir(dir)? {
            let entry = entry?;
            if entry.file_name().to_string_lossy().contains(PART) {
                fs::remove_file(entry.path())?;
            }
        }
        let client = Client::builder()
            .connect_timeout(CONNECT_TIMEOUT)
            .read_timeout(READ_TIMEOUT)
            .redirect(redirect::Policy::custom(|attempt| {
                if attempt.previous().len() >= MAX_REDIRECTS {
                    attempt.error("too many redirects")
                } else if is_https_or_loopback(attempt.url()) {
                    attempt.follow()
                } else {
                    attempt.error("redirect must use https unless it points to loopback")
                }
            }))
            .build()?;
        Ok(Self {
            dir: dir.to_owned(),
            limit: limit_bytes,
            client,
            locks: Mutex::default(),
        })
    }

    pub async fn ensure(&self, url: &Url, digest: &str) -> Result<PathBuf, AgentError> {
        if !valid_digest(digest) {
            return Err(AgentError::Image(
                "digest must be 64 lowercase hex characters".into(),
            ));
        }
        if !is_https_or_loopback(url) {
            return Err(AgentError::Image(format!(
                "{digest}: URL must use https unless it points to loopback"
            )));
        }
        let path = self.dir.join(format!("{digest}.qcow2"));
        let lock = Arc::clone(
            self.locks
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .entry(digest.to_owned())
                .or_default(),
        );
        let _guard = lock.lock().await;
        if tokio::fs::metadata(&path)
            .await
            .is_ok_and(|meta| meta.is_file())
        {
            return Ok(path);
        }
        self.download(url, digest, &path)
            .await
            .map_err(|err| match err {
                AgentError::Image(_) => err,
                other => AgentError::Image(format!("{digest}: {other}")),
            })?;
        Ok(path)
    }

    async fn download(&self, url: &Url, digest: &str, path: &Path) -> Result<(), AgentError> {
        // The part file is 0600 and removed on drop, so every early return cleans up.
        let (file, part) = tempfile::Builder::new()
            .prefix(&format!("{digest}{PART}"))
            .tempfile_in(&self.dir)?
            .into_parts();
        let mut file = tokio::fs::File::from_std(file);
        let mut response = self
            .client
            .get(url.clone())
            .send()
            .await
            .and_then(reqwest::Response::error_for_status)
            .map_err(reqwest::Error::without_url)?;
        let limit = self.limit;
        let over_limit = || AgentError::Image(format!("{digest}: larger than {limit} bytes"));
        if response.content_length().is_some_and(|len| len > limit) {
            return Err(over_limit());
        }
        let mut hasher = Sha256::new();
        let mut size: u64 = 0;
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(reqwest::Error::without_url)?
        {
            size += chunk.len() as u64;
            if size > limit {
                return Err(over_limit());
            }
            hasher.update(&chunk);
            file.write_all(&chunk).await?;
        }
        file.sync_all().await?;
        if hex::encode(hasher.finalize()) != digest {
            return Err(AgentError::Image(format!("{digest}: digest mismatch")));
        }
        part.persist(path).map_err(|err| err.error)?;
        Ok(())
    }
}

fn valid_digest(digest: &str) -> bool {
    digest.len() == 64
        && digest
            .bytes()
            .all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

#[cfg(test)]
mod tests;
