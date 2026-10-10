# 02 - API: the Recording model writes an asciicast

Read `promts/00-README.md` first; its ground rules apply. Then read
`docs/project/05-recording.md` in full, Console store in
`docs/project/04-console.md` and Ephemeral state in
`docs/project/02-domain-model.md`.

## Goal

A Recording can be started, fed output, resizes and markers, and closed,
and the file it leaves is a valid asciicast v2. Nothing calls it yet.

## Established facts

- `models/recording.py` holds a bare row: `link_id`, `url`, `size_bytes`,
  `duration_seconds`.
- `File(path, data, filer_url).append()` appends through the filer or to
  local disk; `File.size()` is 0 for a file that does not exist; `File.url`
  is what a scenario stores as `bundle_url`.
- `models/console.py` shows the house way of raw Valkey keys with a TTL,
  including `_commit` for an untyped pipeline.
- `Console.ready()` is the READY offset or `None`; `Console.since(offset)`
  is the tail from there or `None`; `Console.held(filer_url)` is the log
  end.
- Console frames arrive from one agent socket, markers and resizes from
  other threads of the same process.

## Scope

- `settings.py`: `recording_limit_bytes`, positive, 64 MiB by default.
- `models/recording.py`:
  - the row gains `deployment_id`, indexed; `path` is
    `recordings/<deployment_id>/<id>.cast`; `load` and `save` as on other
    models;
  - `start(console, title, filer_url)`: stores the file's URL and the row,
    writes the header (`version` 2, `width` 80, `height` 24, `timestamp`
    the row's `created_at`, `title`), names itself the deployment's live
    recording, and, when the console has a READY offset, writes the tail
    from it as the first output at time 0 and remembers the log end as
    recorded;
  - `output(offset, data, console, filer_url, limit_bytes)`: writes the
    part of `data` past what is recorded as one `o` event. Before a READY
    offset is known it writes nothing; with one and nothing recorded yet,
    it starts there;
  - `resize(cols, rows, ...)` writes `r` with `<cols>x<rows>`; `mark(text,
    ...)` writes `m`;
  - `close(filer_url)`: stores the file's size and the seconds since
    `created_at` in the row and deletes the Valkey keys; a second call
    changes nothing;
  - a way to read the live recording id of a deployment from Valkey,
    `None` when there is none.
- State under `recording:<id>:*` and `recording:of:<deployment_id>`, one
  day TTL, renewed by every write.
- One event is stamped and appended under a lock of the module, so the
  times in the file never decrease.
- `o` text is the bytes decoded as UTF-8: an incomplete sequence at the end
  waits for the next call, an invalid byte becomes U+FFFD.
- `migrations/versions/v0001_initial.py`: the `recordings` table gains
  `deployment_id` and its index.

## Failure behaviour

- An event that would take the file past `limit_bytes` is not written;
  `recording limit reached` is written as an `m` in its place, once, and
  every later call writes nothing.
- A filer error passes to the caller; the recorded offset does not move,
  so the same bytes are taken again from the next frame that carries them.
- With the Valkey keys lost, the bytes written are read back from the
  file's size and output resumes at the first frame that arrives.
- No event text, title aside, reaches a log line.

## Tests

`tests/test_recordings.py`, against the local file store:

- positive: the header is the first line and parses; output after READY
  becomes `o` events whose text joins to what was appended; a link opened
  after READY has the bytes since READY as its first event at time 0; a
  resize and a marker appear in order with the output; times never
  decrease; a multibyte character split over two calls comes out whole;
  `close` stores the size of the file and a duration;
- negative: output before READY writes nothing; bytes already recorded are
  not written twice when a frame overlaps; an invalid byte becomes U+FFFD
  and the line stays valid JSON; past the limit the marker appears once
  and the file stops growing; a closed recording is no longer the
  deployment's live one.
- `tests/test_migrations.py` keeps passing.

## Acceptance

`.venv/bin/pytest -q packages/api` green; `make smoke` passes;
`.venv/bin/pre-commit run --files` on every edited file passes.
