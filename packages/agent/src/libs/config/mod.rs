use std::fs::{self, DirBuilder};
use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
use std::path::{Path, PathBuf};

use serde::Deserialize;
use url::{Host, Url};

use crate::libs::error::AgentError;

pub const MAX_CAPACITY: u32 = 64;
const DEFAULT_CAPACITY: u32 = 1;
const MAX_LABELS: usize = 16;
const MAX_LABEL: usize = 32;
const DEFAULT_IMAGE_LIMIT_GIB: u32 = 20;
const MAX_IMAGE_LIMIT_GIB: u32 = 512;
const GIB: u64 = 1 << 30;
const DEFAULT_QEMU: &str = "qemu-system-x86_64";
const DEFAULT_QEMU_IMG: &str = "qemu-img";
const DEFAULT_XORRISO: &str = "xorriso";
const DEFAULT_OVMF_CODE: &str = "/usr/share/OVMF/OVMF_CODE_4M.fd";
const DEFAULT_OVMF_VARS: &str = "/usr/share/OVMF/OVMF_VARS_4M.fd";
const PREFIX: &str = "JATSZOTER_AGENT_";
const ENV_FILE: &str = "JATSZOTER_AGENT_ENV_FILE";

#[derive(Debug, Clone)]
pub struct Config {
    pub api_url: Url,
    pub name: String,
    pub labels: Vec<String>,
    pub capacity: u32,
    pub state_dir: PathBuf,
    pub image_limit_bytes: u64,
    pub qemu: PathBuf,
    pub qemu_img: PathBuf,
    pub xorriso: PathBuf,
    pub ovmf_code: PathBuf,
    pub ovmf_vars: PathBuf,
}

/// The environment as read, before any of it is checked.
#[derive(Debug, Default, Deserialize)]
pub struct RawConfig {
    pub api_url: String,
    pub name: String,
    pub labels: Option<String>,
    pub capacity: Option<u32>,
    pub state_dir: Option<PathBuf>,
    pub image_limit_gib: Option<u32>,
    pub qemu: Option<PathBuf>,
    pub qemu_img: Option<PathBuf>,
    pub xorriso: Option<PathBuf>,
    pub ovmf_code: Option<PathBuf>,
    pub ovmf_vars: Option<PathBuf>,
}

impl Config {
    pub fn parse(
        raw: RawConfig,
        cache_home: Option<PathBuf>,
        home: Option<PathBuf>,
    ) -> Result<Self, AgentError> {
        let state_dir = match raw.state_dir {
            Some(dir) => dir,
            None => cache_home
                .or_else(|| home.map(|home| home.join(".cache")))
                .ok_or_else(|| {
                    AgentError::Config("HOME is not set, so the state directory is unknown".into())
                })?
                .join("jatszoter"),
        };
        if !state_dir.is_absolute() {
            return Err(AgentError::Config(format!(
                "{PREFIX}STATE_DIR {} must be an absolute path",
                state_dir.display()
            )));
        }
        let image_limit_gib = raw
            .image_limit_gib
            .map_or(Ok(DEFAULT_IMAGE_LIMIT_GIB), check_image_limit)?;
        Ok(Self {
            api_url: parse_api_url(&raw.api_url)?,
            name: parse_name(&raw.name)?,
            labels: raw.labels.as_deref().map_or(Ok(Vec::new()), parse_labels)?,
            capacity: raw.capacity.map_or(Ok(DEFAULT_CAPACITY), check_capacity)?,
            state_dir,
            image_limit_bytes: u64::from(image_limit_gib) * GIB,
            qemu: raw.qemu.unwrap_or_else(|| DEFAULT_QEMU.into()),
            qemu_img: raw.qemu_img.unwrap_or_else(|| DEFAULT_QEMU_IMG.into()),
            xorriso: raw.xorriso.unwrap_or_else(|| DEFAULT_XORRISO.into()),
            ovmf_code: raw.ovmf_code.unwrap_or_else(|| DEFAULT_OVMF_CODE.into()),
            ovmf_vars: raw.ovmf_vars.unwrap_or_else(|| DEFAULT_OVMF_VARS.into()),
        })
    }

    pub fn prepare(&self) -> Result<(), AgentError> {
        prepare_private_dir(&self.state_dir)
    }
}

pub fn load() -> Result<Config, AgentError> {
    // A .env file is read only when named: one picked up from the working directory could
    // redirect the API URL the agent trusts.
    if let Ok(path) = std::env::var(ENV_FILE) {
        dotenvy::from_path(&path)
            .map_err(|err| AgentError::Config(format!("{ENV_FILE} {path}: {err}")))?;
    }
    let raw: RawConfig = envy::prefixed(PREFIX).from_env().map_err(describe)?;
    let var = |key: &str| {
        std::env::var_os(key)
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
    };
    Config::parse(raw, var("XDG_CACHE_HOME"), var("HOME"))
}

fn describe(err: envy::Error) -> AgentError {
    let envy::Error::MissingValue(field) = err else {
        return AgentError::Config(err.to_string());
    };
    AgentError::Config(format!("{PREFIX}{} is not set", field.to_uppercase()))
}

pub fn prepare_private_dir(path: &Path) -> Result<(), AgentError> {
    DirBuilder::new().recursive(true).mode(0o700).create(path)?;
    let meta = fs::symlink_metadata(path)?;
    if !meta.file_type().is_dir() || meta.permissions().mode() & 0o077 != 0 {
        return Err(AgentError::Config(format!(
            "{} must be a directory, not a symlink, and private to its owner",
            path.display()
        )));
    }
    Ok(())
}

pub fn parse_api_url(raw: &str) -> Result<Url, AgentError> {
    let reject = |why: &str| AgentError::Config(format!("{PREFIX}API_URL {why}"));
    let mut url = Url::parse(raw).map_err(|_| reject("is not a valid URL"))?;
    if !url.username().is_empty() || url.password().is_some() {
        return Err(reject("must not carry credentials"));
    }
    if url.query().is_some() || url.fragment().is_some() {
        return Err(reject("must not carry a query or fragment"));
    }
    if url.host().is_none() {
        return Err(reject("has no host"));
    }
    if !is_https_or_loopback(&url) {
        return Err(reject("must use https unless it points to loopback"));
    }
    if !url.path().ends_with('/') {
        let path = format!("{}/", url.path());
        url.set_path(&path);
    }
    Ok(url)
}

pub fn is_https_or_loopback(url: &Url) -> bool {
    let loopback = match url.host() {
        Some(Host::Ipv4(ip)) => ip.is_loopback(),
        Some(Host::Ipv6(ip)) => ip.is_loopback(),
        Some(Host::Domain(name)) => name == "localhost",
        None => return false,
    };
    match url.scheme() {
        "https" => true,
        "http" => loopback,
        _ => false,
    }
}

fn valid_word(word: &str, max: usize) -> bool {
    let mut chars = word.chars();
    chars.next().is_some_and(|c| c.is_ascii_alphanumeric())
        && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
        && word.len() <= max
}

pub fn parse_name(raw: &str) -> Result<String, AgentError> {
    if valid_word(raw, 64) {
        Ok(raw.to_owned())
    } else {
        Err(AgentError::Config(format!(
            "{PREFIX}NAME {raw:?} is not a valid agent name"
        )))
    }
}

pub fn parse_labels(raw: &str) -> Result<Vec<String>, AgentError> {
    let labels: Vec<String> = raw
        .split(',')
        .map(str::trim)
        .filter(|label| !label.is_empty())
        .map(str::to_owned)
        .collect();
    if labels.len() <= MAX_LABELS && labels.iter().all(|label| valid_word(label, MAX_LABEL)) {
        Ok(labels)
    } else {
        Err(AgentError::Config(format!(
            "{PREFIX}LABELS must list at most {MAX_LABELS} labels of [A-Za-z0-9._-], \
             up to {MAX_LABEL} bytes each"
        )))
    }
}

fn check_capacity(capacity: u32) -> Result<u32, AgentError> {
    if capacity <= MAX_CAPACITY {
        Ok(capacity)
    } else {
        Err(AgentError::Config(format!(
            "{PREFIX}CAPACITY must be an integer in 0..={MAX_CAPACITY}"
        )))
    }
}

fn check_image_limit(gib: u32) -> Result<u32, AgentError> {
    if (1..=MAX_IMAGE_LIMIT_GIB).contains(&gib) {
        Ok(gib)
    } else {
        Err(AgentError::Config(format!(
            "{PREFIX}IMAGE_LIMIT_GIB must be an integer in 1..={MAX_IMAGE_LIMIT_GIB}"
        )))
    }
}

#[cfg(test)]
mod tests;
