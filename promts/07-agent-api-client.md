# 07 - Agent: read desired state, post transitions

Read `promts/00-README.md` first; its ground rules apply. Then read the Agent
protocol section of `docs/project/01-architecture.md`.

## Goal

The agent's HTTP client learns the two new calls. Nothing drives them yet;
prompt 10 does.

## Established facts

- `packages/agent/src/libs/api/mod.rs`: trait `Api` with `register`, `claim`,
  `heartbeat`, `refresh`; `HttpApi::signed` always POSTs to
  `api/v1/agents/{agent_id}/{action}` and signs with `sign(key, ts, method,
  path, body)`; `Signed<T>` is `Accepted(T)` or `Refused` (401).
- `libs/agent/tests.rs` has a `FakeApi` implementing the trait; it must keep
  compiling.
- Tests use `wiremock`.

## Scope

- `libs/api/mod.rs`:
  - `HttpApi::signed` takes the method; GET sends and signs an empty body;
  - types, `Deserialize`, matching the API exactly:
    `Desired { id, vm_id, status: Status, scenario: ScenarioRef, image:
    ImageRef, resources: Resources, timeouts: Timeouts }`, `Status` an enum
    of the eight statuses in upper case, `Event` an enum
    `Assign | Boot | Bootstrap | Destroy | Fail` serialised in lower case;
  - `deployments(agent_id, credential) -> Result<Signed<Option<Vec<Desired>>>>`
    where `None` is the 409 "no live lease";
  - `transition(agent_id, credential, deployment_id, event, expected,
    reason: Option<&str>) -> Result<Signed<Moved>>` with
    `enum Moved { To(Status), Conflict }`, 409 being `Conflict`;
  - 401 is `Signed::Refused` for both, as for heartbeat.
- `libs/agent/tests.rs`: extend `FakeApi` with the two methods (recording
  what it was asked, returning scripted answers).
- Tests in `libs/api/tests.rs`.

## Tests

Positive: the GET is signed over an empty body at the right path (check the
signature the way the existing tests do); a desired-state body with every
field parses; a transition sends `{event, expected, reason}` and reads the
new status.

Negative: 401 is `Refused` for both calls; 409 on deployments is
`Accepted(None)`; 409 on a transition is `Conflict`; an unknown status in the
body is an error, not a panic; a 500 is an `AgentError::Api`.

## Acceptance

`cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D
warnings`, `cargo test -p jatszoter-agent` green.
