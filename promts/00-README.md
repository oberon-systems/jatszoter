# Roadmap item 10 - implementation prompts

Item 10 of `docs/project/07-roadmap.md` (checks through the guest agent),
cut into prompts that run one at a time, in order, each on a clean tree.
Every prompt assumes the previous ones have landed. Item 9's prompts landed
before this set was written; git history keeps them.

| #  | Prompt               | Component | Result                                            |
| -- | -------------------- | --------- | ------------------------------------------------- |
| 01 | `01-docs.md`         | docs      | the item 10 contracts in `docs/`                  |
| 02 | `02-api-checks.md`   | api       | a check is requested, read and timed out          |
| 03 | `03-api-agent.md`    | api       | the agent fetches the script and posts the result |
| 04 | `04-agent-checks.md` | agent     | the agent runs a pending check in the guest       |
| 05 | `05-smoke.md`        | smoke     | a check fails before the fix and passes after it  |

## Decisions behind the cut

Taken with the user on 2026-10-11; prompt 01 writes them into `docs/`.
The docs already said who asks for a check, that the agent sees it in its
desired state, and what a result is; these fill what they left open.

- A check is requested with `POST /api/v1/deployments/{id}/checks` by a
  signed operator, an operator link or a trainee link of that deployment,
  only while the deployment is READY. A viewer link is refused.
- One check runs at a time: while a deployment has a `pending` check, a
  new request answers 409.
- A `pending` check older than the deployment's `check_seconds` plus a
  grace of 30 seconds is settled as `timeout` by the API itself, the next
  time checks are requested or read. A deployment that ends settles its
  `pending` check as `error` with the message `deployment ended`.
- The desired state names the pending check by id only. The agent fetches
  the script with a signed
  `GET /api/v1/agents/{agent_id}/deployments/{id}/checks/{check_id}`,
  guarded like the seed: the holding agent, a live lease, a READY
  deployment, a `pending` check.
- The agent writes the script into the guest, runs it as root, kills it
  once `check_seconds` pass, deletes it, and posts `status`, `exit_code`,
  `message` and `detail`. A result for a check that is no longer `pending`
  answers 409 and changes nothing.
- `message` is the first line of stdout, at most 200 characters. `detail`
  is the start of stderr, at most 4096 bytes, stored in a new column of
  `checks` and shown to an operator only: a trainee or viewer link never
  gets the field.
- Every settled check is written into the deployment's live recording as
  an `m` event: `check <status>: <message>`, or `check <status>` without a
  message. A recording that cannot take it never fails the result.
- `GET /api/v1/deployments/{id}/checks` lists a deployment's checks and
  `GET /api/v1/deployments/{id}/checks/{check_id}` returns one, to a signed
  operator and to any link of that deployment.
- Limits on how often a trainee may ask stay with roadmap item 12.

## Ground rules for every prompt

Each prompt starts by pointing here. These hold for all of them.

- Do not commit, stage, branch or run any writing git command.
- Read `AGENTS.md` and the docs a prompt names before writing code. Where
  the code and a prompt disagree with `docs/`, the docs win; stop and say so.
- Code, comments, docs and messages are in English, UTF-8.
- Comments only where the code is not obvious, at most two lines each.
- No real names of hosts, IPs, users, repositories or paths in code, tests
  or docs: use `alpha`, `beta`, `example.com`, `192.0.2.x`. Loopback
  `127.0.0.1` is fine.
- Off limits: `.claude/`, `.gemini/`, `.codex/`, `target/`, `.venv/`, any
  `.env` file, `CLAUDE.local.md`, `GEMINI.md`.
- Every edited file must pass `.venv/bin/pre-commit run --files <paths>`.
- Python API conventions (see `docs/project/02-domain-model.md`):
  - one SQLModel table per `models/<name>.py`, inheriting `Base`, re-exported
    from `models/__init__.py`; no `Relationship()`, no foreign keys, no
    server-side defaults, no database product named anywhere;
  - a model reads one row with its instance method `load(**key)`, which is
    `self._load(self._session, **key)[0]` - a miss is `IndexError`; a list
    is `X._load(session, **key)`, and related rows are a cached property
    like `Deployment.links`;
  - a model stores itself with `save()`; the caller builds the instance and
    sets its fields, and a model never builds one of itself or of another
    model outside `__init__`;
  - no `@classmethod` outside `models/_base.py`, and no `by_*`, `every`,
    `register` or similar helpers; where a prompt names one, read it as
    `load`, `_load` or `save`;
  - state with a TTL or a cache role lives in Valkey through
    `models/_cache.py`; new helpers for it go in the module that needs them,
    never into `Cached` or `Base` without asking;
  - domain methods live on the model and work on `self`; routes stay thin;
  - models never read settings: `create_app(settings)` builds `Settings`
    once, routes take them from `SettingsDep`, and a model gets what it
    needs (keys, flags, URLs, limits) as arguments;
  - the project is in development: every schema change edits
    `migrations/versions/v0001_initial.py`, no new Alembic revision, and
    `test_head_matches_the_models` must keep passing;
  - errors are `DomainError` subclasses from `errors.py`; the handler in
    `routes/deps.py` maps them to status codes.
- Rust agent conventions: one module per directory under
  `packages/agent/src/libs/` with its `tests.rs`; errors are `AgentError`;
  nothing from a scenario or a trainee passes through a host shell.
- Never log a password, token, link, credential key, signature, the content
  of a seed, a check script, a check's stdout or stderr, console bytes or
  the content of a recording.
- Checks each prompt must leave green (when an agent runs under
  `CLAUDE.local.md`, it asks the user before running them):
  - `.venv/bin/pytest -q packages/api` for API prompts;
  - `cargo test -p jatszoter-agent` and `cargo clippy -p jatszoter-agent`
    for agent prompts;
  - `make smoke` after every prompt; prompt 05 changes it.
- Finish with a short report: files changed, tests added, and every place
  where the prompt had to be interpreted.
