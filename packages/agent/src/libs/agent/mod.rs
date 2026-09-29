use std::time::{Duration, Instant};

use crate::libs::api::{unix_now, Api, Claim, Signed};
use crate::libs::config::Config;
use crate::libs::error::AgentError;
use crate::libs::lease::LeaseClock;
use crate::libs::state::{State, StateStore};

pub const CLAIM_POLL: Duration = Duration::from_secs(10);
const REFRESH_BEFORE_SECONDS: i64 = 15 * 60;

/// What the agent destroys when it fences; the QEMU runtime arrives with roadmap item 4.
pub trait Runtime {
    fn destroy_all(&mut self) -> Result<(), AgentError>;
}

pub struct NoRuntime;

impl Runtime for NoRuntime {
    fn destroy_all(&mut self) -> Result<(), AgentError> {
        Ok(())
    }
}

pub struct Agent<A, R> {
    api: A,
    runtime: R,
    store: StateStore,
    state: State,
    clock: LeaseClock,
    name: String,
    labels: Vec<String>,
    capacity: u32,
}

impl<A: Api, R: Runtime> Agent<A, R> {
    pub fn new(config: &Config, api: A, runtime: R) -> Result<Self, AgentError> {
        let store = StateStore::new(&config.state_dir);
        Ok(Self {
            api,
            runtime,
            state: store.load()?,
            store,
            clock: LeaseClock::default(),
            name: config.name.clone(),
            labels: config.labels.clone(),
            capacity: config.capacity,
        })
    }

    /// One step of the loop; returns how long to wait before the next one.
    pub async fn cycle(&mut self) -> Result<Duration, AgentError> {
        self.fence_if_lapsed(Instant::now())?;
        let (Some(agent_id), Some(credential)) =
            (self.state.agent_id.clone(), self.state.credential.clone())
        else {
            return self.enroll().await;
        };
        let credential = if credential.expires_at - unix_now() <= REFRESH_BEFORE_SECONDS {
            match self.api.refresh(&agent_id, &credential).await? {
                Signed::Accepted(fresh) => {
                    self.state.credential = Some(fresh.clone());
                    self.store.save(&self.state)?;
                    fresh
                }
                Signed::Refused => return self.forget_credential(),
            }
        } else {
            credential
        };
        let sent_at = Instant::now();
        match self
            .api
            .heartbeat(&agent_id, &credential, self.capacity)
            .await?
        {
            Signed::Accepted(lease) => {
                let ttl = u64::try_from(lease.expires_at - lease.heartbeat_at).unwrap_or(0);
                let ttl = Duration::from_secs(ttl);
                self.clock.renewed(sent_at, ttl);
                tracing::debug!(lease_id = %lease.id, capacity = lease.capacity, "lease renewed");
                Ok(ttl / 3)
            }
            Signed::Refused => self.forget_credential(),
        }
    }

    // A refused credential sends the agent back to registration under the id it already has.
    async fn enroll(&mut self) -> Result<Duration, AgentError> {
        let Some(code) = self.state.claim_code.clone() else {
            let code = self
                .api
                .register(&self.name, &self.labels, self.state.agent_id.as_deref())
                .await?;
            self.state.claim_code = Some(code);
            self.store.save(&self.state)?;
            tracing::info!(agent_id = ?self.state.agent_id, "registered, waiting for an operator");
            return Ok(CLAIM_POLL);
        };
        match self.api.claim(&code).await? {
            Claim::Waiting => Ok(CLAIM_POLL),
            Claim::Refused => {
                tracing::warn!("the registration expired; registering again");
                self.state.claim_code = None;
                self.store.save(&self.state)?;
                Ok(Duration::ZERO)
            }
            Claim::Granted {
                agent_id,
                credential,
            } => {
                tracing::info!(%agent_id, "claimed the credential");
                self.state = State {
                    agent_id: Some(agent_id),
                    claim_code: None,
                    credential: Some(credential),
                };
                self.store.save(&self.state)?;
                Ok(Duration::ZERO)
            }
        }
    }

    fn forget_credential(&mut self) -> Result<Duration, AgentError> {
        tracing::warn!("the API refused the credential; registering again");
        self.state.credential = None;
        self.store.save(&self.state)?;
        Ok(Duration::ZERO)
    }

    fn fence_if_lapsed(&mut self, now: Instant) -> Result<(), AgentError> {
        if self.clock.expired(now) {
            tracing::warn!("the lease lapsed; fencing");
            self.runtime.destroy_all()?;
            self.clock.clear();
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests;
