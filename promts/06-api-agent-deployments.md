# 06 - API: assignment on heartbeat, desired state, agent transitions

Read `promts/00-README.md` first; its ground rules apply. Then read the Agent
protocol section of `docs/project/01-architecture.md` and Ephemeral state in
`docs/project/02-domain-model.md`.

## Goal

The API side of the agent loop: a heartbeat assigns PENDING deployments to
the agent within its capacity, the agent reads its desired state, and it
moves its deployments with signed transitions.

## Established facts

- `Agent.heartbeat(capacity)` renews the lease in the cache and, when the
  previous lease had lapsed, aborts the agent's STARTING..STOPPING
  deployments first. `Agent.lease` reads it without renewing.
- `Agent.deployments` reads `Deployment._load(...)` for its agent, the way
  related rows are read (like `Deployment.links`).
- Deployments carry `agent_id` (the operator may pin one at create) and
  `vm_id`, both nullable.
- `require_agent` verifies a signed request and works for GET with an empty
  body.

## Scope

- `models/deployment.py`:
  - `assign(agent_id)`: compare-and-swap like `transition` - refresh with
    `with_for_update`, succeed only while still PENDING and `agent_id` is
    empty or already this agent; sets `agent_id` and a fresh `vm_id` when it
    has none. Returns whether it took it.
- `models/agent.py`:
  - `deployments` keeps the cached-property pattern, its non-terminal rows
    being what the agent holds;
  - `heartbeat` assigns after renewing: free slots = capacity minus held
    non-terminal deployments; pinned PENDING first (they are already held and
    need only a `vm_id`), then the waiting ones - `Deployment._load` of
    PENDING with no agent, oldest first - until the slots run out; a lost
    CAS is skipped, not an error;
  - `desired()`: the entries below for every held non-terminal deployment.
- `routes/agents.py`:
  - `GET /agents/{agent_id}/deployments`: 409 without a live lease, else a
    list of `{id, vm_id, status, scenario: {id, slug, version},
    image: {name, url, digest}, resources: {cpus, memory_mib, disk_gib},
    timeouts: {bootstrap_seconds, check_seconds, session_minutes}}`;
  - `POST /agents/{agent_id}/deployments/{deployment_id}/transition`, strict
    body `{event, expected, reason?}`; `event` one of `assign`, `boot`,
    `bootstrap`, `destroy`, `fail`, anything else 422; 409 without a live
    lease or when the compare-and-swap fails; 404 for an unknown deployment
    or one held by another agent; 200 `{id, status}` otherwise, repeats
    included.
- Tests in `test_agents.py` and `test_agents_api.py`.

## Failure behaviour

Two agents heartbeating at once never both hold one deployment. An agent that
lost its lease can neither read desired state nor move anything until a new
heartbeat opens a lease, and by then its held deployments have been aborted.

## Tests

Positive: capacity 2 with three waiting deployments assigns the oldest two;
a pinned deployment goes to its agent even when an older unpinned one waits;
desired state carries the image URL and digest from `image_id`; `assign`,
then `fail` with a reason, from the agent.

Negative: capacity 0 assigns nothing; a pinned deployment is never assigned
to another agent; desired state and transitions without a live lease get
409; a transition with the wrong `expected` gets 409; `fail` without a reason
is refused; another agent's deployment gets 404; `stop` as an agent event gets
422.

## Acceptance

`.venv/bin/pytest -q packages/api` green; pre-commit clean on every changed
file.
