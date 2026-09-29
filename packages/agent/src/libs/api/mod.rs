use std::time::{Duration, SystemTime, UNIX_EPOCH};

use hmac::{Hmac, Mac};
use reqwest::{Client, Response, StatusCode};
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
        agent_id: &str,
        credential: &Credential,
        action: &str,
        body: Vec<u8>,
    ) -> Result<Response, AgentError> {
        let url = self.url(&format!("api/v1/agents/{agent_id}/{action}"))?;
        let timestamp = unix_now().to_string();
        let signature = sign(&credential.key, &timestamp, "POST", url.path(), &body)?;
        Ok(self
            .client
            .post(url)
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
        let response = self.signed(agent_id, credential, "heartbeat", body).await?;
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
            .signed(agent_id, credential, "credential", Vec::new())
            .await?;
        if response.status() == StatusCode::UNAUTHORIZED {
            return Ok(Signed::Refused);
        }
        Ok(Signed::Accepted(
            accepted(response, StatusCode::OK)?.json().await?,
        ))
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
