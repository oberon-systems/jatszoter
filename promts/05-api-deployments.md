# 05 - API: operator deployments and stop before READY

Read `promts/00-README.md` first; its ground rules apply. Then read the
Deployment lifecycle and Who moves what sections of
`docs/project/02-domain-model.md` and the Stop data flow in
`docs/project/01-architecture.md`.

## Goal

The operator creates a deployment from a registered scenario, lists and reads
deployments, and stops one. Stopping works from STARTING and BOOTSTRAPPING as
well as READY.

## Established facts

- `lifecycle.py` is a python-statemachine `Lifecycle`: one event per row of
  the table, each with a condition on the actor. `stop` today is
  `READY -> STOPPING` by the operator; `cancel` is `PENDING -> CANCELLED`.
- `Deployment.transition(event, expected, reason, actor=, agent_id=)` is
  compare-and-swap and a repeat of the same move is a no-op.
- `Deployment` copies `cpus`, `memory_mib`, `disk_gib`, `bootstrap_seconds`,
  `check_seconds`, `session_minutes` at create time.
- `Scenario` and `Image` read a row with `load(**key)` (prompt 04); the
  route loads them with `Scenario(session=session).load(id=...)`.

## Scope

- `lifecycle.py`: `stop` allowed from STARTING, BOOTSTRAPPING and READY, by
  the operator. Keep `expire` READY only. Update `tests/test_lifecycle.py`.
- `models/deployment.py`:
  - `image_id: uuid.UUID`, added to `v0001_initial.py`;
  - `fill(scenario, image, defaults)`: resources and timeouts from
    `scenario.document`, the missing ones from `defaults` (the settings the
    route passes), `image_id` from `image`;
  - the route builds `Deployment(session=..., scenario_id=..., agent_id=...)`,
    calls `fill` and then `save`; a row is read with `load(id=...)`, the
    list with `Deployment._load(session, ...)` filtered by status.
- Settings: `default_cpus=1`, `default_memory_mib=1024`, `default_disk_gib=10`,
  `default_bootstrap_seconds=900`, `default_check_seconds=30`,
  `default_session_minutes=60`.
- Agents are read with `Agent(session=session).load(id=...)`; nothing new in
  `models/agent.py`.
- `routes/deployments.py`, operator only, mounted under `/api/v1`:
  - `POST /deployments` `{scenario_id, agent_id?}`: 201; unknown scenario
    404; `agent_id` naming no enabled agent 422;
  - `GET /deployments?status=`: newest first;
  - `GET /deployments/{id}`: 404 when unknown;
  - `POST /deployments/{id}/stop`: PENDING sends `cancel`, STARTING,
    BOOTSTRAPPING and READY send `stop`, anything else 409; a stop of an
    already STOPPING or CANCELLED deployment is 200, not an error.
  - `DeploymentOut`: `id`, `scenario_id`, `image_id`, `agent_id`, `vm_id`,
    `status`, `reason`, the resources and timeouts, `created_at`,
    `updated_at`, `ready_at`, `ended_at`.
- Tests: `test_deployments_api.py`, plus model tests in `test_deployments.py`.

## Failure behaviour

A stop racing an agent transition gets 409 from the compare-and-swap and
changes nothing; the operator retries.

## Tests

Positive: create copies scenario values and falls back to settings for
missing ones; pinned `agent_id` is stored; list filters by status; stop from
each of PENDING, STARTING, BOOTSTRAPPING, READY lands in the right state.

Negative: unknown scenario 404; disabled or unknown agent 422; stop of a
COMPLETED or FAILED deployment 409; the agent cannot send `stop`, the
operator cannot send `boot`; a trainee's signed request 403.

## Acceptance

`.venv/bin/pytest -q packages/api` green; pre-commit clean on every changed
file.
