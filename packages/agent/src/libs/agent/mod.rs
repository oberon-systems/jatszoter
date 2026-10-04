use std::sync::Arc;
use std::time::{Duration, Instant};

use tokio::sync::RwLock;
use tokio::time::timeout;

use crate::libs::api::{unix_now, Api, Claim, Desired, Signed};
use crate::libs::config::Config;
use crate::libs::error::AgentError;
use crate::libs::lease::LeaseClock;
use crate::libs::state::{Credential, State, StateStore};

pub const CLAIM_POLL: Duration = Duration::from_secs(10);
const REFRESH_BEFORE_SECONDS: i64 = 15 * 60;
const AUTH_WAIT: Duration = Duration::from_secs(20);

#[derive(Clone)]
pub struct Signer {
    pub agent_id: String,
    pub credential: Credential,
}

/// The credential the runtime's tasks sign with; a refresh swaps it under the write lock.
pub type Auth = Arc<RwLock<Option<Signer>>>;

/// The VMs on the host: matched to the desired state, destroyed when the agent fences.
pub(crate) trait Runtime {
    /// Spawns the work the desired state calls for and returns at once.
    fn reconcile(&mut self, _auth: &Auth, _desired: Vec<Desired>) {}
    fn destroy_all(&mut self) -> Result<(), AgentError>;
}

pub struct Agent<A, R> {
    api: A,
    runtime: R,
    auth: Auth,
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
        let state = store.load()?;
        Ok(Self {
            api,
            runtime,
            auth: Arc::new(RwLock::new(signer(&state))),
            state,
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
                    self.set_auth(Some(Signer {
                        agent_id: agent_id.clone(),
                        credential: fresh.clone(),
                    }))
                    .await?;
                    self.state.credential = Some(fresh.clone());
                    self.store.save(&self.state)?;
                    fresh
                }
                Signed::Refused => return self.forget_credential().await,
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
                match self.api.deployments(&agent_id, &credential).await {
                    Ok(Signed::Accepted(Some(desired))) => {
                        self.runtime.reconcile(&self.auth, desired)
                    }
                    Ok(Signed::Accepted(None)) => {
                        tracing::debug!("no live lease; skipping the round")
                    }
                    Ok(Signed::Refused) => return self.forget_credential().await,
                    Err(err) => tracing::warn!(error = %err, "reading the desired state failed"),
                }
                Ok(ttl / 3)
            }
            Signed::Refused => self.forget_credential().await,
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
                let state = State {
                    agent_id: Some(agent_id),
                    claim_code: None,
                    credential: Some(credential),
                };
                self.set_auth(signer(&state)).await?;
                self.state = state;
                self.store.save(&self.state)?;
                Ok(Duration::ZERO)
            }
        }
    }

    async fn forget_credential(&mut self) -> Result<Duration, AgentError> {
        tracing::warn!("the API refused the credential; registering again");
        self.set_auth(None).await?;
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

    // Waits only for transitions already in flight, each bounded by the runtime.
    async fn set_auth(&self, signer: Option<Signer>) -> Result<(), AgentError> {
        let mut auth = timeout(AUTH_WAIT, self.auth.write()).await.map_err(|_| {
            AgentError::Api("in-flight transitions still hold the credential".into())
        })?;
        *auth = signer;
        Ok(())
    }
}

fn signer(state: &State) -> Option<Signer> {
    Some(Signer {
        agent_id: state.agent_id.clone()?,
        credential: state.credential.clone()?,
    })
}

#[cfg(test)]
mod tests;
