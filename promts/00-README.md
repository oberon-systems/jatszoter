# Roadmap item 9 - implementation prompts

Item 9 of `docs/project/07-roadmap.md` (recording and replay), cut into
prompts that run one at a time, in order, each on a clean tree. Every prompt
assumes the previous ones have landed. Item 8's prompts landed before this
set was written; git history keeps them.

| #  | Prompt                     | Component | Result                                          |
| -- | -------------------------- | --------- | ----------------------------------------------- |
| 01 | `01-docs.md`               | docs      | the item 9 contracts in `docs/`                 |
| 02 | `02-api-recording.md`      | api       | the Recording model writes an asciicast         |
| 03 | `03-api-recording-feed.md` | api       | open, console, resize, control, revoke feed it  |
| 04 | `04-api-replay.md`         | api       | the operator lists recordings and downloads one |
| 05 | `05-smoke.md`              | smoke     | a session leaves a recording that replays       |

## Decisions behind the cut

Taken with the user on 2026-10-10; prompt 01 writes them into `docs/`.

- Item 9 ships the core: the API writes the asciicast, closes it and serves
  it to the operator. Check results as markers move to item 10, the
  vendored asciinema-player and its jump points to item 11, and
  `JATSZOTER_RECORDING_RETENTION_DAYS` with its sweep to item 12. The agent
  does not change.
- The first open of a trainee link stores a Recording row and writes the
  header line. Links of other roles record nothing. Time zero is the row's
  `created_at`, read from the API clock; an event's time is the API clock
  minus it.
- The file is `recordings/<deployment_id>/<recording_id>.cast` in the file
  store, written one event per filer append. The row keeps the file's URL,
  as a scenario keeps its bundle's, and gains `deployment_id`.
- Output is recorded from the READY offset, the place a trainee's terminal
  starts. Nothing is written before READY. A link first opened after READY
  gets the bytes from the READY offset to the log end as its first event,
  at time 0; when the tail no longer holds them, the recording starts at
  the log end.
- An `o` event is text: console bytes decoded as UTF-8, a sequence split
  between two frames carried over to the next, an invalid byte written as
  U+FFFD.
- The header says 80 by 24, the size of a serial line nobody has resized.
  An `r` event, `<cols>x<rows>`, is written when the API passes a resize of
  the holder of control to the agent; the API cannot see whether the guest
  applied it.
- An `m` event `control operator` or `control trainee` is written when the
  control route changes the holder.
- Events are stamped and appended one at a time, so their times never go
  backwards.
- `JATSZOTER_RECORDING_LIMIT_BYTES`, 64 MiB by default: the event that
  would pass it is replaced by the marker `recording limit reached`, and
  nothing is written after that.
- The recorder is the Recording model, `models/recording.py`. What it needs
  between events lives in Valkey under `recording:<id>:*` with a one-day
  TTL renewed on every write: how far the console is recorded, the bytes
  written, the split UTF-8 rest, the limit flag. `recording:of:<deployment
  id>` names the deployment's live recording.
- Revoking the trainee link closes its recording: the row gets the file's
  final size and the duration, and the Valkey keys go. A deployment that
  ends revokes its links and closes the recording the same way.
- `GET /api/v1/deployments/{id}/recordings` lists a deployment's recordings
  and `GET /api/v1/recordings/{recording_id}.cast` returns one, live or
  closed, as far as it is written. Both take a signed operator request
  only; a link never reads a recording.
- Keys are never recorded: there is no `i` event, and nothing a viewer
  sends reaches the file except a resize's size.

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
- Never log a password, token, link, credential key, signature, the content
  of a seed, console bytes or the content of a recording: both carry what
  the guest prints, which includes what the trainee typed.
- Checks each prompt must leave green (when an agent runs under
  `CLAUDE.local.md`, it asks the user before running them):
  - `.venv/bin/pytest -q packages/api` for API prompts;
  - `make smoke` after every prompt; prompt 05 changes it.
- Finish with a short report: files changed, tests added, and every place
  where the prompt had to be interpreted.
