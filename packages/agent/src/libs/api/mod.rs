use std::time::{Duration, SystemTime, UNIX_EPOCH};

use hmac::{Hmac, Mac};
use reqwest::{Client, Method, Response, StatusCode};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use url::Url;

use crate::libs::error::AgentError;
use crate::libs::state::Credential;

type HmacSha256 = Hmac<Sha256>;

const TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Lease {
    pub id: String,
    pub capacity: u32,
    pub heartbeat_at: i64,
    pub expires_at: i64,
}

#[derive(Debug, PartialEq, Eq)]
pub enum Claim {
    Waiting,
    Refused,
    Granted {
        agent_id: String,
        credential: Credential,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum Status {
    Pending,
    Starting,
    Bootstrapping,
    Ready,
    Stopping,
    Completed,
    Failed,
    Cancelled,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Event {
    Assign,
    #[allow(dead_code, reason = "sent by the bootstrap, roadmap item 6")]
    Boot,
    #[allow(dead_code, reason = "sent by the bootstrap, roadmap item 6")]
    Bootstrap,
    Destroy,
    Fail,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct ScenarioRef {
    pub id: String,
    pub slug: String,
    pub version: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct ImageRef {
    pub name: String,
    pub url: String,
    pub digest: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Resources {
    pub cpus: u32,
    pub memory_mib: u64,
    pub disk_gib: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Timeouts {
    pub bootstrap_seconds: u64,
    pub check_seconds: u64,
    pub session_minutes: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Desired {
    pub id: String,
    pub vm_id: String,
    pub status: Status,
    pub scenario: ScenarioRef,
    pub image: ImageRef,
    pub resources: Resources,
    pub timeouts: Timeouts,
}

/// A deployment's NoCloud seed; it carries the scenario's steps, so `Debug` prints no content.
#[derive(Clone, Deserialize)]
pub struct Seed {
    pub user_data: String,
    pub meta_data: String,
}

impl std::fmt::Debug for Seed {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Seed").finish_non_exhaustive()
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Moved {
    To(Status),
    Conflict,
}

/// A signed call goes through, or the API no longer accepts the credential.
#[derive(Debug, PartialEq, Eq)]
pub enum Signed<T> {
    Accepted(T),
    Refused,
}

pub(crate) trait Api {
    async fn register(
        &self,
        name: &str,
        labels: &[String],
        agent_id: Option<&str>,
    ) -> Result<String, AgentError>;
    async fn claim(&self, code: &str) -> Result<Claim, AgentError>;
    async fn heartbeat(
        &self,
        agent_id: &str,
        credential: &Credential,
        capacity: u32,
    ) -> Result<Signed<Lease>, AgentError>;
    async fn refresh(
        &self,
        agent_id: &str,
        credential: &Credential,
    ) -> Result<Signed<Credential>, AgentError>;
    /// `None` is the 409 an agent without a live lease gets.
    async fn deployments(
        &self,
        agent_id: &str,
        credential: &Credential,
    ) -> Result<Signed<Option<Vec<Desired>>>, AgentError>;
    async fn transition(
        &self,
        agent_id: &str,
        credential: &Credential,
        deployment_id: &str,
        event: Event,
        expected: Status,
        reason: Option<&str>,
    ) -> Result<Signed<Moved>, AgentError>;
    /// `None` is the 409 of a deployment that is not STARTING under a live lease.
    async fn seed(
        &self,
        _agent_id: &str,
        _credential: &Credential,
        _deployment_id: &str,
    ) -> Result<Signed<Option<Seed>>, AgentError> {
        Ok(Signed::Accepted(None))
    }
}

#[derive(Serialize)]
struct RegisterIn<'a> {
    name: &'a str,
    labels: &'a [String],
    #[serde(skip_serializing_if = "Option::is_none")]
    agent_id: Option<&'a str>,
}

#[derive(Deserialize)]
struct RegisterOut {
    claim_code: String,
}

#[derive(Serialize)]
struct ClaimIn<'a> {
    code: &'a str,
}

#[derive(Deserialize)]
struct ClaimOut {
    agent_id: String,
    credential: Credential,
}

#[derive(Serialize)]
struct HeartbeatIn {
    capacity: u32,
}

#[derive(Serialize)]
struct TransitionIn<'a> {
    event: Event,
    expected: Status,
    #[serde(skip_serializing_if = "Option::is_none")]
    reason: Option<&'a str>,
}

#[derive(Deserialize)]
struct TransitionOut {
    status: Status,
}

#[derive(Clone)]
pub struct HttpApi {
    client: Client,
    base: Url,
}

impl HttpApi {
    pub fn new(base: Url) -> Result<Self, AgentError> {
        Ok(Self {
            client: Client::builder().timeout(TIMEOUT).build()?,
            base,
        })
    }

    fn url(&self, path: &str) -> Result<Url, AgentError> {
        self.base
            .join(path)
            .map_err(|err| AgentError::Api(format!("{path}: {err}")))
    }

    async fn signed(
        &self,
        method: Method,
        agent_id: &str,
        credential: &Credential,
        action: &str,
        body: Vec<u8>,
    ) -> Result<Response, AgentError> {
        let url = self.url(&format!("api/v1/agents/{agent_id}/{action}"))?;
        let timestamp = unix_now().to_string();
        let signature = sign(
            &credential.key,
            &timestamp,
            method.as_str(),
            url.path(),
            &body,
        )?;
        Ok(self
            .client
            .request(method, url)
            .header("Content-Type", "application/json")
            .header("X-Credential", &credential.credential)
            .header("X-Timestamp", timestamp)
            .header("X-Signature", signature)
            .body(body)
            .send()
            .await?)
    }
}

impl Api for HttpApi {
    async fn register(
        &self,
        name: &str,
        labels: &[String],
        agent_id: Option<&str>,
    ) -> Result<String, AgentError> {
        let response = self
            .client
            .post(self.url("api/v1/agents/register")?)
            .json(&RegisterIn {
                name,
                labels,
                agent_id,
            })
            .send()
            .await?;
        let out: RegisterOut = accepted(response, StatusCode::CREATED)?.json().await?;
        Ok(out.claim_code)
    }

    async fn claim(&self, code: &str) -> Result<Claim, AgentError> {
        let response = self
            .client
            .post(self.url("api/v1/agents/claim")?)
            .json(&ClaimIn { code })
            .send()
            .await?;
        match response.status() {
            StatusCode::CONFLICT => return Ok(Claim::Waiting),
            StatusCode::UNAUTHORIZED => return Ok(Claim::Refused),
            _ => {}
        }
        let response = accepted(response, StatusCode::OK)?;
        let signature = response
            .headers()
            .get("X-Signature")
            .and_then(|value| value.to_str().ok())
            .and_then(|value| hex::decode(value).ok())
            .ok_or_else(|| AgentError::Api("the claim response carries no signature".into()))?;
        let body = response.bytes().await?;
        let mut check = mac(code.as_bytes())?;
        check.update(&body);
        check
            .verify_slice(&signature)
            .map_err(|_| AgentError::Api("the claim response signature does not match".into()))?;
        let out: ClaimOut = serde_json::from_slice(&body)
            .map_err(|err| AgentError::Api(format!("claim response: {err}")))?;
        Ok(Claim::Granted {
            agent_id: out.agent_id,
            credential: out.credential,
        })
    }

    async fn heartbeat(
        &self,
        agent_id: &str,
        credential: &Credential,
        capacity: u32,
    ) -> Result<Signed<Lease>, AgentError> {
        let body = serde_json::to_vec(&HeartbeatIn { capacity })
            .map_err(|err| AgentError::Api(err.to_string()))?;
        let response = self
            .signed(Method::POST, agent_id, credential, "heartbeat", body)
            .await?;
        if response.status() == StatusCode::UNAUTHORIZED {
            return Ok(Signed::Refused);
        }
        Ok(Signed::Accepted(
            accepted(response, StatusCode::OK)?.json().await?,
        ))
    }

    async fn refresh(
        &self,
        agent_id: &str,
        credential: &Credential,
    ) -> Result<Signed<Credential>, AgentError> {
        let response = self
            .signed(Method::POST, agent_id, credential, "credential", Vec::new())
            .await?;
        if response.status() == StatusCode::UNAUTHORIZED {
            return Ok(Signed::Refused);
        }
        Ok(Signed::Accepted(
            accepted(response, StatusCode::OK)?.json().await?,
        ))
    }

    async fn deployments(
        &self,
        agent_id: &str,
        credential: &Credential,
    ) -> Result<Signed<Option<Vec<Desired>>>, AgentError> {
        let response = self
            .signed(Method::GET, agent_id, credential, "deployments", Vec::new())
            .await?;
        match response.status() {
            StatusCode::UNAUTHORIZED => Ok(Signed::Refused),
            StatusCode::CONFLICT => Ok(Signed::Accepted(None)),
            _ => Ok(Signed::Accepted(Some(
                accepted(response, StatusCode::OK)?.json().await?,
            ))),
        }
    }

    async fn transition(
        &self,
        agent_id: &str,
        credential: &Credential,
        deployment_id: &str,
        event: Event,
        expected: Status,
        reason: Option<&str>,
    ) -> Result<Signed<Moved>, AgentError> {
        let body = serde_json::to_vec(&TransitionIn {
            event,
            expected,
            reason,
        })
        .map_err(|err| AgentError::Api(err.to_string()))?;
        let action = format!("deployments/{deployment_id}/transition");
        let response = self
            .signed(Method::POST, agent_id, credential, &action, body)
            .await?;
        match response.status() {
            StatusCode::UNAUTHORIZED => Ok(Signed::Refused),
            StatusCode::CONFLICT => Ok(Signed::Accepted(Moved::Conflict)),
            _ => {
                let out: TransitionOut = accepted(response, StatusCode::OK)?.json().await?;
                Ok(Signed::Accepted(Moved::To(out.status)))
            }
        }
    }

    async fn seed(
        &self,
        agent_id: &str,
        credential: &Credential,
        deployment_id: &str,
    ) -> Result<Signed<Option<Seed>>, AgentError> {
        let action = format!("deployments/{deployment_id}/seed");
        let response = self
            .signed(Method::GET, agent_id, credential, &action, Vec::new())
            .await?;
        match response.status() {
            StatusCode::UNAUTHORIZED => Ok(Signed::Refused),
            StatusCode::CONFLICT => Ok(Signed::Accepted(None)),
            _ => {
                let body = accepted(response, StatusCode::OK)?.bytes().await?;
                // A decode error may quote the body, so it is not passed on.
                let seed = serde_json::from_slice(&body)
                    .map_err(|_| AgentError::Api("the seed response is not a seed".into()))?;
                Ok(Signed::Accepted(Some(seed)))
            }
        }
    }
}

fn accepted(response: Response, expected: StatusCode) -> Result<Response, AgentError> {
    if response.status() == expected {
        Ok(response)
    } else {
        Err(AgentError::Api(format!(
            "{} answered {}",
            response.url().path(),
            response.status()
        )))
    }
}

fn mac(key: &[u8]) -> Result<HmacSha256, AgentError> {
    HmacSha256::new_from_slice(key).map_err(|err| AgentError::Api(err.to_string()))
}

/// HMAC-SHA256 of `ts\nMETHOD\npath\nsha256(body)` under the credential's key, as in 06.
pub fn sign(
    key: &str,
    timestamp: &str,
    method: &str,
    path: &str,
    body: &[u8],
) -> Result<String, AgentError> {
    let message = format!(
        "{timestamp}\n{method}\n{path}\n{}",
        hex::encode(Sha256::digest(body))
    );
    let mut signer = mac(key.as_bytes())?;
    signer.update(message.as_bytes());
    Ok(hex::encode(signer.finalize().into_bytes()))
}

pub fn unix_now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |elapsed| {
            i64::try_from(elapsed.as_secs()).unwrap_or(i64::MAX)
        })
}

#[cfg(test)]
mod tests;
