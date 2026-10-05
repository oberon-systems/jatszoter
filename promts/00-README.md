# Roadmap item 6 - implementation prompts

Item 6 of `docs/project/07-roadmap.md` (cloud-init bootstrap and the
bootstrap marker), cut into prompts that run one at a time, in order, each on
a clean tree. Every prompt assumes the previous ones have landed. Item 4's
prompts landed before this set was written; git history keeps them.

| #  | Prompt                    | Component | Result                                              |
| -- | ------------------------- | --------- | --------------------------------------------------- |
| 01 | `01-docs.md`              | docs      | contracts below written into `docs/`                |
| 02 | `02-scenario-rocky.md`    | scenarios | `nginx-502` version 2 on `rocky-9`                  |
| 03 | `03-api-seed.md`          | api       | the API renders a deployment's NoCloud seed         |
| 04 | `04-agent-qga.md`         | agent     | guest agent client: ping, read a file               |
| 05 | `05-agent-seed-iso.md`    | agent     | seed fetched, packed into an ISO, attached, ejected |
| 06 | `06-agent-bootstrap.md`   | agent     | STARTING to BOOTSTRAPPING to READY, or FAILED       |
| 07 | `07-smoke.md`             | smoke     | a Rocky 9 deployment reaches READY and is stopped   |

## Decisions behind the cut

Taken with the user on 2026-10-04; prompt 01 writes them into `docs/`.

- The API renders the seed. A signed agent route returns the NoCloud
  `user-data` and `meta-data` of one deployment, built in Python from the
  stored bundle the API already parses and validates. The agent never parses
  a scenario; it packs the two files into an ISO.
- The seed is served only while the deployment is STARTING and only to the
  agent that holds it: it carries the break step.
- The agent packs the ISO with `xorriso -as mkisofs`, spawned with an
  argument vector like `qemu-img`; its path is
  `JATSZOTER_AGENT_XORRISO`, default `xorriso`. It must be installed on every
  agent host and on the smoke host, never in the guest.
- The guest target is Rocky Linux 9: the image is registered as `rocky-9`
  and comes from the GenericCloud qcow2. Rocky 10 can follow as a second
  image later.
- On RHEL-family guests `qemu-guest-agent` blocks `guest-file-*` and
  `guest-exec*` by default. The seed allows them and restarts the guest
  agent before anything polls it; item 10's checks need `guest-exec` too.
- `scenarios/nginx-502` moves to Rocky as version 2: nginx config in
  `conf.d`, nginx enabled, and the SELinux boolean that lets nginx proxy to
  a local port.
- The smoke test downloads a pinned Rocky 9 GenericCloud qcow2 once, checks
  its sha256, keeps it in a cache outside `$TEMP_DIR`, and serves it from
  loopback like the blank disk before.
- The bootstrap deadline counts from the VM's creation time in `vm.json`,
  so an agent restart neither resets nor loses it.

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
- Never log a password, token, link, credential key, signature, or the
  content of a seed: it carries the scenario's steps.
- Checks each prompt must leave green (when an agent runs under
  `CLAUDE.local.md`, it asks the user before running them):
  - `.venv/bin/pytest -q packages/api` for API prompts;
  - `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets --
    -D warnings` and `cargo test -p jatszoter-agent` for agent prompts;
  - `make smoke` after every prompt; prompt 07 changes what it proves.
- Finish with a short report: files changed, tests added, and every place
  where the prompt had to be interpreted.
