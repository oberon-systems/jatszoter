# Roadmap item 7 - implementation prompts

Item 7 of `docs/project/07-roadmap.md` (the console pipeline with bounded
viewer queues), cut into prompts that run one at a time, in order, each on a
clean tree. Every prompt assumes the previous ones have landed. Item 6's
prompts landed before this set was written; git history keeps them.

| #  | Prompt                       | Component | Result                                              |
| -- | ---------------------------- | --------- | --------------------------------------------------- |
| 01 | `01-docs.md`                 | docs      | contracts below written into `docs/`                |
| 02 | `02-api-file-append.md`      | api       | the file store appends to a file                    |
| 03 | `03-api-console-store.md`    | api       | console tail in Valkey, full log in the file store  |
| 04 | `04-api-console-sockets.md`  | api       | agent socket, hub, operator attach, log download    |
| 05 | `05-agent-console.md`        | agent     | the serial console shipped over the agent socket    |
| 06 | `06-smoke.md`                | smoke     | an operator viewer receives console output          |

## Decisions behind the cut

Taken with the user on 2026-10-08, after the runner console of the sibling
project naos; prompt 01 writes them into `docs/`.

- QEMU writes the serial console into `console.log` in the VM directory
  itself (`logfile=`, `logappend=on` on the serial chardev), from boot and
  whether or not a client is connected. That file is the agent's buffer: the
  agent keeps no ring of its own.
- The agent holds one WebSocket per live VM whose deployment is STARTING,
  BOOTSTRAPPING or READY, to
  `WS /api/v1/agents/{agent_id}/deployments/{id}/console`, signed like every
  agent request. It ships the whole file from offset 0, so the operator sees
  the boot; the API marks where READY begins.
- A frame from the agent is binary: an 8-byte big-endian offset, then up to
  64 KiB read from `console.log` at that offset. The API answers text
  `{"offset": n}`, how far it holds the log, once on connect and after every
  frame; the agent moves its offset to `n`, backwards too. The agent looks
  at the file every 50 ms and reconnects every 5 s. There is no HTTP
  fallback.
- The API keeps two copies. The last `JATSZOTER_CONSOLE_TAIL_BYTES` (8 MiB)
  of each deployment's console sit in Valkey; a viewer that connects gets
  only that tail, and anything older lives in the browsers that saw it. The
  full log is appended at once to the file store as
  `consoles/<deployment_id>.log`, up to `JATSZOTER_CONSOLE_LIMIT_BYTES`
  (64 MiB); past it the API closes the agent's socket with code 4413 and the
  agent stops shipping that deployment.
- The file store gains append: the SeaweedFS filer's `POST ?op=append`, and
  the same on the local `data/storage` directory.
- The log end at the moment the `bootstrap` transition is accepted is the
  deployment's READY offset, kept in Valkey. Item 8's trainee view starts
  there.
- The fan-out hub lives in the API process. In this item
  `WS /api/v1/deployments/{id}/attach` serves signed operator requests only:
  the tail, then every new byte. Each viewer's queue is capped by
  `JATSZOTER_CONSOLE_VIEWER_BUFFER_BYTES` (4 MiB); a viewer past the cap is
  resent from the Valkey tail at its last offset, or closed when that is
  gone. Keys, resize, links and control are item 8.
- `GET /api/v1/deployments/{id}/console` gives an operator the full log. A
  deployment reaching a terminal status drops its Valkey keys; the file
  stays until item 12's sweeps.
- The agent speaks WebSocket through `tokio-tungstenite` over rustls. The
  API serves it through uvicorn with the `websockets` package, which the
  smoke viewer uses as well.

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
  - `make smoke` after every prompt; prompt 06 changes what it proves.
- Finish with a short report: files changed, tests added, and every place
  where the prompt had to be interpreted.
