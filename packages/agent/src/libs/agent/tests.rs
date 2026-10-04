use std::collections::VecDeque;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tempfile::TempDir;
use url::Url;

use super::{Agent, Runtime, CLAIM_POLL};
use crate::libs::api::{unix_now, Api, Claim, Desired, Event, Lease, Moved, Signed, Status};
use crate::libs::config::Config;
use crate::libs::error::AgentError;
use crate::libs::state::{Credential, State, StateStore};

type Transition = (String, Event, Status, Option<String>);

#[derive(Default)]
struct FakeApi {
    claims: Mutex<VecDeque<Claim>>,
    beats: Mutex<VecDeque<Signed<Lease>>>,
    refreshes: Mutex<VecDeque<Signed<Credential>>>,
    registered: Mutex<Vec<Option<String>>>,
    signed_with: Mutex<Vec<String>>,
    desired: Mutex<VecDeque<Signed<Option<Vec<Desired>>>>>,
    moves: Mutex<VecDeque<Signed<Moved>>>,
    listed: Mutex<Vec<String>>,
    transitions: Mutex<Vec<Transition>>,
}

impl Api for FakeApi {
    async fn register(
        &self,
        _name: &str,
        _labels: &[String],
        agent_id: Option<&str>,
    ) -> Result<String, AgentError> {
        self.registered
            .lock()
            .unwrap()
            .push(agent_id.map(str::to_owned));
        Ok("delta".into())
    }

    async fn claim(&self, _code: &str) -> Result<Claim, AgentError> {
        Ok(self
            .claims
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or(Claim::Waiting))
    }

    async fn heartbeat(
        &self,
        _agent_id: &str,
        credential: &Credential,
        capacity: u32,
    ) -> Result<Signed<Lease>, AgentError> {
        self.signed_with
            .lock()
            .unwrap()
            .push(credential.credential.clone());
        Ok(self
            .beats
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or(Signed::Accepted(Lease {
                id: "epsilon".into(),
                capacity,
                heartbeat_at: 100,
                expires_at: 130,
            })))
    }

    async fn refresh(
        &self,
        _agent_id: &str,
        _credential: &Credential,
    ) -> Result<Signed<Credential>, AgentError> {
        Ok(self
            .refreshes
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or(Signed::Refused))
    }

    async fn deployments(
        &self,
        agent_id: &str,
        _credential: &Credential,
    ) -> Result<Signed<Option<Vec<Desired>>>, AgentError> {
        self.listed.lock().unwrap().push(agent_id.to_owned());
        Ok(self
            .desired
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or(Signed::Accepted(Some(Vec::new()))))
    }

    async fn transition(
        &self,
        _agent_id: &str,
        _credential: &Credential,
        deployment_id: &str,
        event: Event,
        expected: Status,
        reason: Option<&str>,
    ) -> Result<Signed<Moved>, AgentError> {
        self.transitions.lock().unwrap().push((
            deployment_id.to_owned(),
            event,
            expected,
            reason.map(str::to_owned),
        ));
        Ok(self
            .moves
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or(Signed::Accepted(Moved::Conflict)))
    }
}

#[derive(Default)]
struct CountingRuntime {
    destroyed: u32,
}

impl Runtime for CountingRuntime {
    fn destroy_all(&mut self) -> Result<(), AgentError> {
        self.destroyed += 1;
        Ok(())
    }
}

fn credential(name: &str, expires_in: i64) -> Credential {
    Credential {
        credential: name.into(),
        expires_at: unix_now() + expires_in,
        key: "beta".into(),
    }
}

fn agent(dir: &TempDir, api: FakeApi, state: State) -> Agent<FakeApi, CountingRuntime> {
    let config = Config {
        api_url: Url::parse("https://api.example.com/").unwrap(),
        name: "alpha".into(),
        labels: Vec::new(),
        capacity: 2,
        state_dir: dir.path().to_path_buf(),
        image_limit_bytes: 1 << 30,
        qemu: "qemu-system-x86_64".into(),
        qemu_img: "qemu-img".into(),
    };
    StateStore::new(dir.path()).save(&state).unwrap();
    Agent::new(&config, api, CountingRuntime::default()).unwrap()
}

fn enrolled() -> State {
    State {
        agent_id: Some("gamma".into()),
        claim_code: None,
        credential: Some(credential("current:1:1", 3600)),
    }
}

#[tokio::test]
async fn a_new_agent_registers_and_keeps_its_code() {
    let dir = tempfile::tempdir().unwrap();
    let mut agent = agent(&dir, FakeApi::default(), State::default());

    assert_eq!(agent.cycle().await.unwrap(), CLAIM_POLL);

    assert_eq!(*agent.api.registered.lock().unwrap(), vec![None]);
    let saved = StateStore::new(dir.path()).load().unwrap();
    assert_eq!(saved.claim_code.as_deref(), Some("delta"));
}

#[tokio::test]
async fn a_granted_claim_stores_the_id_and_credential() {
    let dir = tempfile::tempdir().unwrap();
    let api = FakeApi::default();
    api.claims.lock().unwrap().extend([
        Claim::Waiting,
        Claim::Granted {
            agent_id: "gamma".into(),
            credential: credential("current:1:1", 3600),
        },
    ]);
    let mut agent = agent(
        &dir,
        api,
        State {
            claim_code: Some("delta".into()),
            ..State::default()
        },
    );

    assert_eq!(agent.cycle().await.unwrap(), CLAIM_POLL);
    agent.cycle().await.unwrap();

    let saved = StateStore::new(dir.path()).load().unwrap();
    assert_eq!(saved.agent_id.as_deref(), Some("gamma"));
    assert!(saved.claim_code.is_none());
    assert!(saved.credential.is_some());
}

#[tokio::test]
async fn an_expired_registration_is_made_again() {
    let dir = tempfile::tempdir().unwrap();
    let api = FakeApi::default();
    api.claims.lock().unwrap().push_back(Claim::Refused);
    let mut agent = agent(
        &dir,
        api,
        State {
            claim_code: Some("delta".into()),
            ..State::default()
        },
    );

    agent.cycle().await.unwrap();
    agent.cycle().await.unwrap();

    assert_eq!(*agent.api.registered.lock().unwrap(), vec![None]);
}

#[tokio::test]
async fn a_heartbeat_renews_the_lease_a_third_of_its_ttl_ahead() {
    let dir = tempfile::tempdir().unwrap();
    let mut agent = agent(&dir, FakeApi::default(), enrolled());

    assert_eq!(agent.cycle().await.unwrap(), Duration::from_secs(10));
    assert!(!agent.clock.expired(Instant::now()));
}

#[tokio::test]
async fn a_refused_credential_registers_again_under_the_same_id() {
    let dir = tempfile::tempdir().unwrap();
    let api = FakeApi::default();
    api.beats.lock().unwrap().push_back(Signed::Refused);
    let mut agent = agent(&dir, api, enrolled());

    agent.cycle().await.unwrap();
    agent.cycle().await.unwrap();

    assert_eq!(
        *agent.api.registered.lock().unwrap(),
        vec![Some("gamma".to_owned())]
    );
    let saved = StateStore::new(dir.path()).load().unwrap();
    assert_eq!(saved.agent_id.as_deref(), Some("gamma"));
    assert!(saved.credential.is_none());
}

#[tokio::test]
async fn a_credential_due_for_refresh_is_replaced_before_the_heartbeat() {
    let dir = tempfile::tempdir().unwrap();
    let api = FakeApi::default();
    api.refreshes
        .lock()
        .unwrap()
        .push_back(Signed::Accepted(credential("current:2:2", 3600)));
    let mut agent = agent(
        &dir,
        api,
        State {
            credential: Some(credential("current:1:1", 60)),
            ..enrolled()
        },
    );

    agent.cycle().await.unwrap();

    assert_eq!(
        *agent.api.signed_with.lock().unwrap(),
        vec!["current:2:2".to_owned()]
    );
    let saved = StateStore::new(dir.path()).load().unwrap();
    assert_eq!(saved.credential.unwrap().credential, "current:2:2");
}

#[tokio::test]
async fn a_lapsed_lease_fences_the_agent_once() {
    let dir = tempfile::tempdir().unwrap();
    let api = FakeApi::default();
    api.beats.lock().unwrap().push_back(Signed::Refused);
    let mut agent = agent(&dir, api, enrolled());
    let long_ago = Instant::now().checked_sub(Duration::from_secs(60)).unwrap();
    agent.clock.renewed(long_ago, Duration::from_secs(30));

    agent.cycle().await.unwrap();
    agent.cycle().await.unwrap();

    assert_eq!(agent.runtime.destroyed, 1);
}
