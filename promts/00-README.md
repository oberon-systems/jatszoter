# Roadmap item 8 - implementation prompts

Item 8 of `docs/project/07-roadmap.md` (links and take control), cut into
prompts that run one at a time, in order, each on a clean tree. Every prompt
assumes the previous ones have landed. Item 7's prompts landed before this
set was written; git history keeps them.

| #  | Prompt                      | Component | Result                                             |
| -- | --------------------------- | --------- | -------------------------------------------------- |
| 01 | `01-docs.md`                | docs      | resize proven in a guest, contracts in `docs/`     |
| 02 | `02-api-users-links.md`     | api       | users, the trainee link on create, issue, revoke   |
| 03 | `03-api-link-session.md`    | api       | a link opens a cookie session and attaches by role |
| 04 | `04-api-control-keys.md`    | api       | the control holder, keys and resize to the agent   |
| 05 | `05-agent-keys-resize.md`   | agent     | keys into the serial socket, resize through qga    |
| 06 | `06-smoke.md`               | smoke     | a trainee link types; take control cuts its input  |

## Decisions behind the cut

Taken with the user on 2026-10-09; prompt 01 writes them into `docs/`.

- Users are made by the operator: `POST` and `GET /api/v1/users` with name,
  role and comment. Such a user is `enabled` at once and has no password,
  so it cannot log in; it exists to own links. `users.name` is unique.
- `POST /api/v1/deployments` requires `trainee_id`, an enabled user with
  role `trainee`, and answers with the deployment and its trainee link, the
  plain token included that one time. Operator and viewer links are issued
  by `POST /api/v1/deployments/{id}/links` with `user_id`; a deployment
  holds one live trainee link at a time. `POST /api/v1/links/{id}/revoke`
  revokes one.
- `GET /s/{token}`, served by the API outside `/api/v1`, exchanges the
  token for a session cookie and answers 303 to `/t/{deployment_id}`; the
  page there arrives with the web UI in item 11. An unknown or revoked link
  and a disabled user get the same 404.
- The cookie `jatszoter_link` is HttpOnly, Secure, SameSite=Strict, without
  an expiry, and scoped to `Path=/api/v1/deployments/{id}`, so it reaches
  that deployment's attach, control and later checks and nothing else. Its
  value is a random session token; Valkey maps its sha256 to the link id
  for a day, renewed on use. Every use loads the link row, so a revoked
  link ends all its sessions.
- CSRF tokens arrive with the web UI in item 11. Until then a request or an
  upgrade that authenticates by cookie is bound by SameSite=Strict and by
  its `Origin`, which, when sent, must name the request's own host.
- `/s/{token}` never reaches a log line: the access log shows
  `/s/<redacted>`.
- A link attaches to `WS /api/v1/deployments/{id}/attach` only while the
  deployment is READY. An operator link sees what a signed operator sees.
  A trainee or viewer link starts at the READY offset: an older `offset` is
  raised to it, and without a READY offset it starts at the log end.
  Revoking a link closes its sockets with 4403.
- The control holder is `trainee` or `operator`, kept in Valkey beside the
  console tail as `console:<id>:control`, `trainee` when absent, dropped
  with the tail. `POST /api/v1/deployments/{id}/control` sets it for a
  signed operator or an operator link while the deployment is READY. Every
  viewer hears `{"control": ...}` after its first offset and on a change.
- A viewer's binary frame, at most 4096 bytes, is keys; its text frame
  `{"cols": n, "rows": n}`, both 1 to 500, is a resize. The API forwards
  either only from a socket whose role is the holder: a signed operator and
  an operator link are `operator`, a trainee link is `trainee`, a viewer
  link never types. Without an agent socket the frame is dropped.
- The API passes them down the agent's console socket: keys as a binary
  frame, a resize as the same text frame. Up to 256 messages wait for a
  slow agent; more are dropped.
- The agent connects `serial.sock` on the first key and keeps it until its
  console socket to the API ends, reading and discarding what the guest
  echoes there, since `console.log` already carries it. Keys that cannot be
  written are lost; the console socket lives on.
- The agent applies a resize through the guest agent: `guest-exec` of
  `stty -F /dev/ttyS0 cols N rows M` as an argument vector, within 5 s, and
  only when the size differs from the last one applied. Prompt 01 proves
  this in a Rocky 9 guest under SELinux before anything is written; if the
  guest refuses, the set stops there.
- Recording markers for control changes and resizes belong to item 9.

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
- Rust agent conventions: `unsafe_code` is forbidden and clippy runs with
  `-D warnings`; tests sit in `tests.rs` next to `mod.rs`; every host process
  is spawned with an argument vector, never through a shell. A trait change
  keeps the existing tests compiling unchanged where it can, for example
  through a default method.
- Never log a password, token, link, credential key, signature, the content
  of a seed, or console bytes: the console carries what the trainee types
  and what the guest prints.
- Checks each prompt must leave green (when an agent runs under
  `CLAUDE.local.md`, it asks the user before running them):
  - `.venv/bin/pytest -q packages/api` for API prompts;
  - `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets --
    -D warnings` and `cargo test -p jatszoter-agent` for agent prompts;
  - `make smoke` after every prompt; prompts 02 and 06 change it.
- Finish with a short report: files changed, tests added, and every place
  where the prompt had to be interpreted.
