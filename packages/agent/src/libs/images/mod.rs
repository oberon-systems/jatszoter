use std::collections::{HashMap, HashSet};
use std::fs::{self, File};
use std::io::ErrorKind;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime};

use ipnet::IpNet;
use reqwest::dns::{Addrs, Name, Resolve, Resolving};
use reqwest::{redirect, Client};
use sha2::{Digest, Sha256};
use tokio::io::AsyncWriteExt;
use url::{Host, Url};

use crate::libs::config::{is_https_or_loopback, prepare_private_dir};
use crate::libs::error::AgentError;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const READ_TIMEOUT: Duration = Duration::from_secs(60);
const MAX_REDIRECTS: usize = 10;
const PART: &str = ".part-";
const SUFFIX: &str = ".qcow2";
const SCHEME_REFUSED: &str = "URL must use https unless it points to loopback";
const ADDRESS_REFUSED: &str =
    "address is neither public nor in JATSZOTER_AGENT_IMAGE_INTERNAL_CIDRS";

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
struct Refused(&'static str);

#[derive(Clone)]
struct AddressRule(Arc<[IpNet]>);

impl AddressRule {
    fn allows(&self, ip: IpAddr) -> bool {
        let ip = ip.to_canonical();
        is_public(ip) || self.0.iter().any(|net| net.contains(&ip))
    }

    // A name passes here: the resolver checks what it resolves to.
    fn allows_url(&self, url: &Url) -> bool {
        match url.host() {
            Some(Host::Ipv4(ip)) => self.allows(IpAddr::V4(ip)),
            Some(Host::Ipv6(ip)) => self.allows(IpAddr::V6(ip)),
            Some(Host::Domain(_)) => true,
            None => false,
        }
    }
}

// The connector dials only what this returns, so a name cannot rebind past the check.
impl Resolve for AddressRule {
    fn resolve(&self, name: Name) -> Resolving {
        let rule = self.clone();
        Box::pin(async move {
            let addrs: Vec<SocketAddr> =
                tokio::net::lookup_host((name.as_str(), 0)).await?.collect();
            if let Some(addr) = addrs.iter().find(|addr| !rule.allows(addr.ip())) {
                tracing::warn!(
                    host = name.as_str(),
                    address = %addr.ip(),
                    "an image host resolves to a refused address"
                );
                return Err(Refused(ADDRESS_REFUSED).into());
            }
            Ok(Box::new(addrs.into_iter()) as Addrs)
        })
    }
}

fn is_public(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(ip) => is_public_v4(ip),
        IpAddr::V6(ip) => is_public_v6(ip),
    }
}

fn is_public_v4(ip: Ipv4Addr) -> bool {
    !matches!(
        ip.octets(),
        [0 | 10 | 127, ..]
            | [100, 64..=127, ..]
            | [169, 254, ..]
            | [172, 16..=31, ..]
            | [192, 0, 0 | 2, _]
            | [192, 88, 99, _]
            | [192, 168, ..]
            | [198, 18 | 19, ..]
            | [198, 51, 100, _]
            | [203, 0, 113, _]
            | [224..=255, ..]
    )
}

fn is_public_v6(ip: Ipv6Addr) -> bool {
    matches!(ip.segments(), [0x2000..=0x3fff, ..])
        && !matches!(
            ip.segments(),
            [0x2001, 0..=0x1ff, ..] | [0x2001, 0xdb8, ..] | [0x2002, ..] | [0x3fff, 0..=0xfff, ..]
        )
}

// reqwest's Display drops the source, which holds the only useful part of a refusal.
fn download_error(digest: &str, err: reqwest::Error) -> AgentError {
    let mut source = std::error::Error::source(&err);
    while let Some(cause) = source {
        if let Some(refused) = cause.downcast_ref::<Refused>() {
            return AgentError::Image(format!("{digest}: {refused}"));
        }
        source = cause.source();
    }
    err.without_url().into()
}

struct Cached {
    path: PathBuf,
    digest: String,
    size: u64,
    used: SystemTime,
}

pub struct ImageCache {
    dir: PathBuf,
    limit: u64,
    cap: u64,
    wanted: Mutex<HashSet<String>>,
    // Bytes promised to downloads in flight, whose part files the scan leaves out.
    reserved: Mutex<u64>,
    deadline: Duration,
    client: Client,
    rule: AddressRule,
    locks: Mutex<HashMap<String, Arc<tokio::sync::Mutex<()>>>>,
}

impl ImageCache {
    pub fn new(
        dir: &Path,
        limit_bytes: u64,
        cap_bytes: u64,
        deadline: Duration,
        internal_cidrs: &[IpNet],
        use_proxy: bool,
    ) -> Result<Self, AgentError> {
        prepare_private_dir(dir)?;
        for entry in fs::read_dir(dir)? {
            let entry = entry?;
            if entry.file_name().to_string_lossy().contains(PART) {
                fs::remove_file(entry.path())?;
            }
        }
        let rule = AddressRule(internal_cidrs.into());
        let followed = rule.clone();
        let builder = Client::builder()
            .connect_timeout(CONNECT_TIMEOUT)
            .read_timeout(READ_TIMEOUT)
            .referer(false)
            .dns_resolver(rule.clone())
            .redirect(redirect::Policy::custom(move |attempt| {
                if attempt.previous().len() >= MAX_REDIRECTS {
                    attempt.error("too many redirects")
                } else if !is_https_or_loopback(attempt.url()) {
                    attempt.error(Refused(SCHEME_REFUSED))
                } else if !followed.allows_url(attempt.url()) {
                    attempt.error(Refused(ADDRESS_REFUSED))
                } else {
                    attempt.follow()
                }
            }));
        // Through a proxy the resolver sees the proxy's name, not the image host's.
        let builder = if use_proxy {
            tracing::warn!(
                "JATSZOTER_AGENT_IMAGE_USE_PROXY is on: names of image hosts are not checked \
                 against the address rule, the proxy decides what they reach"
            );
            builder
        } else {
            builder.no_proxy()
        };
        Ok(Self {
            dir: dir.to_owned(),
            limit: limit_bytes,
            cap: cap_bytes,
            wanted: Mutex::default(),
            reserved: Mutex::default(),
            deadline,
            client: builder.build()?,
            rule,
            locks: Mutex::default(),
        })
    }

    /// The digests the deployments of this agent use; eviction never takes one of them.
    pub fn keep(&self, digests: impl IntoIterator<Item = String>) {
        *self
            .wanted
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = digests.into_iter().collect();
    }

    pub async fn ensure(&self, url: &Url, digest: &str) -> Result<PathBuf, AgentError> {
        if !valid_digest(digest) {
            return Err(AgentError::Image(
                "digest must be 64 lowercase hex characters".into(),
            ));
        }
        if !is_https_or_loopback(url) {
            return Err(AgentError::Image(format!("{digest}: {SCHEME_REFUSED}")));
        }
        if !self.rule.allows_url(url) {
            return Err(AgentError::Image(format!("{digest}: {ADDRESS_REFUSED}")));
        }
        let path = self.dir.join(format!("{digest}{SUFFIX}"));
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
            if let Err(err) =
                File::open(&path).and_then(|file| file.set_modified(SystemTime::now()))
            {
                tracing::warn!(%digest, error = %err, "marking a cached image as used failed");
            }
            return Ok(path);
        }
        let deadline = self.deadline;
        tokio::time::timeout(deadline, self.download(url, digest, &path))
            .await
            .map_err(|_| {
                AgentError::Image(format!("{digest}: download took longer than {deadline:?}"))
            })?
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
            .map_err(|err| download_error(digest, err))?;
        let limit = self.limit;
        let over_limit = || AgentError::Image(format!("{digest}: larger than {limit} bytes"));
        let length = response.content_length();
        if length.is_some_and(|len| len > limit) {
            return Err(over_limit());
        }
        let _room = self.make_room(digest, length.unwrap_or(limit))?;
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

    fn make_room(&self, digest: &str, need: u64) -> Result<Room<'_>, AgentError> {
        let mut reserved = self
            .reserved
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let wanted = self
            .wanted
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone();
        let cached = self.cached()?;
        let mut used: u64 = cached.iter().map(|image| image.size).sum();
        for image in cached
            .iter()
            .filter(|image| !wanted.contains(&image.digest))
        {
            if used + *reserved + need <= self.cap {
                break;
            }
            fs::remove_file(&image.path).or_else(|err| match err.kind() {
                ErrorKind::NotFound => Ok(()),
                _ => Err(err),
            })?;
            tracing::info!(digest = %image.digest, size = image.size, "evicted an unused image");
            used -= image.size;
        }
        if used + *reserved + need > self.cap {
            let cap = self.cap;
            return Err(AgentError::Image(format!(
                "{digest}: the image cache is full: {need} more bytes do not fit in {cap}, \
                 {used} held by images deployments use and {reserved} by downloads",
                reserved = *reserved
            )));
        }
        *reserved += need;
        Ok(Room {
            cache: self,
            bytes: need,
        })
    }

    /// Completed images, least recently used first.
    fn cached(&self) -> Result<Vec<Cached>, AgentError> {
        let mut cached = Vec::new();
        for entry in fs::read_dir(&self.dir)? {
            let entry = entry?;
            let name = entry.file_name().to_string_lossy().into_owned();
            let Some(digest) = name.strip_suffix(SUFFIX).filter(|stem| valid_digest(stem)) else {
                continue;
            };
            let meta = match entry.metadata() {
                Err(err) if err.kind() == ErrorKind::NotFound => continue,
                other => other?,
            };
            if meta.is_file() {
                cached.push(Cached {
                    path: entry.path(),
                    digest: digest.to_owned(),
                    size: meta.len(),
                    used: meta.modified()?,
                });
            }
        }
        cached.sort_by_key(|image| image.used);
        Ok(cached)
    }
}

struct Room<'a> {
    cache: &'a ImageCache,
    bytes: u64,
}

impl Drop for Room<'_> {
    fn drop(&mut self) {
        *self
            .cache
            .reserved
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) -= self.bytes;
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
