# Roadmap item 4 - implementation prompts

Item 4 of `docs/project/07-roadmap.md` (QEMU runtime), cut into prompts that
run one at a time, in order, each on a clean tree. Every prompt assumes the
previous ones have landed.

| #  | Prompt                          | Component | Result                                           |
| -- | ------------------------------- | --------- | ------------------------------------------------ |
| 01 | `01-docs.md`                    | docs      | contracts below written into `docs/`             |
| 02 | `02-api-operator-login.md`      | api       | users log in, operator requests are signed       |
| 03 | `03-api-file-store.md`          | api       | SeaweedFS client, local directory without one    |
| 04 | `04-api-images-scenarios.md`    | api       | images and scenario bundles (upload or URL)      |
| 05 | `05-api-deployments.md`         | api       | operator creates, lists and stops deployments    |
| 06 | `06-api-agent-deployments.md`   | api       | heartbeat assigns, desired state, transitions    |
| 07 | `07-agent-api-client.md`        | agent     | the agent reads desired state, posts transitions |
| 08 | `08-agent-image-cache.md`       | agent     | base images downloaded once, checked by digest   |
| 09 | `09-agent-qemu-vm.md`           | agent     | one VM: create, inspect, destroy, QMP            |
| 10 | `10-agent-reconcile.md`         | agent     | the runtime driven by desired state              |
| 11 | `11-smoke.md`                   | smoke     | assigned, started and destroyed on stop          |

## Decisions behind the cut

Taken with the user on 2026-09-30; prompt 01 writes them into `docs/`.

- Operator login comes first. There is no operator surface without it, so
  item 4 opens with user login and signed operator requests.
- The scenario registry (the API half of item 5) moves into item 4. A
  scenario is registered from an uploaded bundle or from a URL the API
  fetches. Bundles live in SeaweedFS like recordings; without a SeaweedFS
  address the API writes them to a local directory.
- The image moves into the desired state now: the operator registers an
  image (name, URL, sha256), a deployment copies its image at create time,
  the desired state carries URL and digest, and the agent's cache checks the
  digest. Item 5 is marked merged into 4.
- The operator may stop a STARTING or BOOTSTRAPPING deployment, not only a
  READY one: it moves to STOPPING and the agent destroys the VM. Until item 6
  a deployment never leaves STARTING, so without this nothing could stop it.
- The smoke test runs real QEMU (`qemu-system-x86_64`, `qemu-img`) with KVM
  when `/dev/kvm` is usable and TCG otherwise, on a blank qcow2 served from
  loopback.
- Trainee links are not created with a deployment yet; that is item 8.
- The scenario schema is part of the API version and lives in the API
  package. There is no `common` package: it was removed as never needed, so
  no prompt adds shared code outside `api`, `web` and `agent`.

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
  - `X._load(session, **key)` is `filter_by` only and is called only inside
    model `X`; everything else calls classmethods on the model (`by_id`,
    `held_by`, ...);
  - domain methods live on the model; routes stay thin;
  - a new NOT NULL column is added nullable, filled with an `UPDATE`, then
    made NOT NULL inside `batch_alter_table`; each prompt that changes the
    schema adds its own Alembic revision after the current head, and
    `test_head_matches_the_models` must keep passing;
  - errors are `DomainError` subclasses from `errors.py`; the handler in
    `routes/deps.py` maps them to status codes.
- Rust agent conventions: `unsafe_code` is forbidden and clippy runs with
  `-D warnings`; tests sit in `tests.rs` next to `mod.rs`; every host process
  is spawned with an argument vector, never through a shell.
- Never log a password, token, link, credential key or signature.
- Checks each prompt must leave green (when an agent runs under
  `CLAUDE.local.md`, it asks the user before running them):
  - `.venv/bin/pytest -q packages/api` for API prompts;
  - `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets --
    -D warnings` and `cargo test -p jatszoter-agent` for agent prompts;
  - `make smoke` for prompt 11, and it must still pass after 02-10.
- Finish with a short report: files changed, tests added, and every place
  where the prompt had to be interpreted.
