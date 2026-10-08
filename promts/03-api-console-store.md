# 03 - API: the console store

Read `promts/00-README.md` first; its ground rules apply. Then read the
console sections prompt 01 wrote into `docs/project/04-console.md`,
`packages/api/src/jatszoter_api/models/_cache.py`,
`models/deployment.py`, `file.py` and `settings.py`.

## Goal

One object answers for a deployment's console: how far the API holds it,
appending what the agent sends to the full log and the Valkey tail, reading
the tail for a viewer, marking where READY begins and dropping the tail
when the deployment ends. Prompt 04 puts sockets on top of it.

## Established facts

- `Cached` in `models/_cache.py` wraps the Valkey client, an in-memory
  `fakeredis` when `JATSZOTER_VALKEY_DSN` is unset; its `_get` and `_set`
  pickle values, so raw bytes need the client directly
  (`Cached._client()`), from the console module.
- Prompt 02 gave `File.append()` and `File.size()`.
- Every status change goes through `Deployment.transition`, the operator
  stop and the lease expiry included; the agent's `bootstrap` event moves
  BOOTSTRAPPING to READY.
- `TooLargeError` exists in `errors.py` and maps to 413.
- One agent socket writes a deployment's console at a time (prompt 04
  replaces an older socket with a newer one), so the store has a single
  writer per deployment and no compare-and-swap.

## Scope

- `settings.py`: `console_tail_bytes` (8 MiB) and `console_limit_bytes`
  (64 MiB), env `JATSZOTER_CONSOLE_TAIL_BYTES` and
  `JATSZOTER_CONSOLE_LIMIT_BYTES`, both positive.
- `models/console.py`: `Console(Cached)`, not a table, built with the
  deployment id; re-exported from `models/__init__.py`. Valkey keys under
  `console:<deployment_id>:` - `tail` (bytes), `start` (offset of the tail's
  first byte), `end` (offset after its last) and `ready`. The full log is
  `File(f"consoles/{deployment_id}.log", ...)`.
  - `held(filer_url) -> int`: `end` from Valkey; when it is missing, the
    full log's `size()`, written back as `end` and `start` with an empty
    tail.
  - `append(offset, data, filer_url, tail_bytes, limit_bytes) -> (int,
    bytes)`: the bytes from `held()` on are new; an `offset` past `held()`
    writes nothing. New bytes past `limit_bytes` are cut; with nothing left
    under the limit it raises `TooLargeError`. It appends to the full log
    first, then to the tail, trims the tail to its last `tail_bytes` and
    moves `start` and `end`, the tail keys in one `MULTI`. It returns where
    the new bytes start and the bytes themselves, which prompt 04 publishes.
  - `since(offset) -> bytes | None`: the tail from `offset` to `end`; `None`
    when `offset` is older than `start`. `tail() -> (int, bytes)`: the whole
    tail and where it starts.
  - `mark_ready(filer_url)`: `ready` = `held()`. `ready() -> int | None`.
  - `drop()`: deletes the four keys; the full log stays.
  - Every write gives the keys a TTL of one day, so a tail whose `drop` was
    missed frees itself.
- `Deployment.transition`: after an accepted move to READY, `mark_ready`;
  after an accepted move to COMPLETED, FAILED or CANCELLED, `drop`. The
  model gets the filer URL as an argument where it needs one, it never reads
  settings.

## Failure behaviour

- A full-log append that fails raises and leaves the tail and `end`
  untouched, so the agent's next frame retries the same bytes.
- Valkey lost (an API restart on the in-memory fake) costs the tail only:
  `held()` recovers the end from the full log, and a viewer sees new bytes
  from there.
- Console bytes never reach a log line, an exception message or an error
  response.

## Tests

`packages/api/tests/test_console.py`, against the disk filer in `tmp_path`
and the fake cache:

- positive: appends in order land in the log and the tail; a repeated
  frame from an older offset adds only its new part; the tail keeps exactly
  the last `tail_bytes` and `start` follows; `since` inside the tail returns
  the rest; `held` after dropping the Valkey keys equals the log size;
  `mark_ready` records the end; a deployment transition to READY marks it
  and one to COMPLETED drops the keys and keeps the file;
- negative: an offset past `held` writes nothing and answers `held`; a
  frame crossing `limit_bytes` is cut at the limit and the next one raises
  `TooLargeError`; `since` older than `start` is `None`; a failing log append
  leaves the tail and `end` as they were.

## Acceptance

`.venv/bin/pytest -q packages/api` green; `.venv/bin/pre-commit run
--files` on the edited files passes; `make smoke` still passes.
