# 03 - API: the agent fetches the script and posts the result

Read `promts/00-README.md` first; its ground rules apply. Then read Agent
protocol in `docs/project/01-architecture.md`, Check in
`docs/project/03-scenario-format.md`, Writing in
`docs/project/05-recording.md`, and `routes/checks.py` and
`models/check.py` as prompt 02 left them.

## Goal

The agent learns of a pending check, gets its script, and the result it
posts is stored and marked in the recording.

## Established facts

- `Agent.desired` builds one entry per held deployment; `DesiredOut` in
  `routes/agents.py` validates it.
- The `seed` route in `routes/agents.py` reads the stored bundle with
  `bundles.read`, compares its digest with the scenario's, and answers
  only the holding agent under a live lease.
- A scenario's document names its check script under `check`.
- `_recorded` in `routes/console.py` hands the deployment's live recording
  to a writer and logs a failure without raising; `Recording.mark` writes
  an `m` event.

## Scope

- `models/agent.py`, `routes/agents.py`: a desired-state entry gains
  `check`, the id of the deployment's pending check or null.
- `routes/agents.py`:
  - `GET /agents/{agent_id}/deployments/{id}/checks/{check_id}` answers
    `{"script": "...", "timeout_seconds": n}`, the script as text from
    the stored bundle and the deployment's `check_seconds`;
  - `POST /agents/{agent_id}/deployments/{id}/checks/{check_id}` takes
    `status` (`passed`, `failed`, `error`, `timeout`), `exit_code`,
    `message` and `detail`, and settles the check.
- Storing: `message` is cut to its first line and 200 characters,
  `detail` to 4096 bytes on a character boundary; `finished_at` is the
  API clock.
- The marker: every settled check, this route's and the ones prompt 02
  settles as `timeout` or `error`, is written to the deployment's live
  recording as `check <status>: <message>`, or `check <status>` without a
  message. Move `_recorded` where both route modules can use it; ask
  before adding a new module for it.

## Failure behaviour

- Both routes answer 404 for an unknown deployment or check and for
  another agent's deployment, and 409 without a live lease, for a
  deployment that is not READY, and for a check that is not pending.
- `passed` with an exit code other than 0, `failed` with 0 or none, and
  `timeout` or `error` with an exit code are 422.
- A stored bundle that does not read, or whose digest changed, is a 500
  as on the seed route; the check stays pending.
- A recording that cannot take the marker never fails the result: the
  check is stored and the agent gets its answer.
- The script, `message` and `detail` are never logged.

## Tests

`tests/test_agents_api.py`, `tests/test_checks_api.py`:

- positive: the desired state names the pending check and null without
  one; the script answer holds the bundle's check script and the
  deployment's timeout; each of the four statuses is stored with its
  exit code; a long message and a long detail are cut; the recording of
  an opened trainee link holds the marker, with and without a message, in
  order with a control marker; an overdue check settled by a read is
  marked too;
- negative: another agent, no lease, a deployment that is not READY and a
  settled check are refused on both routes; the inconsistent bodies are
  422; a second result changes nothing; with the file store failing the
  result is still stored.

## Acceptance

`.venv/bin/pytest -q packages/api` green; `make smoke` passes;
`.venv/bin/pre-commit run --files` on every edited file passes.
