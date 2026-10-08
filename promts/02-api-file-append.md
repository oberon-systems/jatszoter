# 02 - API: append to a stored file

Read `promts/00-README.md` first; its ground rules apply. Then read
`packages/api/src/jatszoter_api/file.py`, `packages/api/tests/test_files.py`
and the console storage section prompt 01 wrote into
`docs/project/04-console.md`.

## Goal

`File` can add bytes to the end of a stored file and say how long a stored
file is, on the SeaweedFS filer and on the local directory alike, so prompt
03 can write the full console log as it arrives and find its end again.

## Established facts

- `File(path, data, filer_url)` talks to the SeaweedFS filer over HTTP with
  `put`, `get` and `delete`. Without a filer URL it uses `DiskFiler`, an
  `httpx` transport that answers the same requests from `data/storage/`
  under the working directory.
- `DiskFiler._write` creates missing directories 0700 and replaces the
  target atomically through a temporary file.
- The SeaweedFS filer appends to a file with `POST <path>?op=append` and the
  bytes as the body; a missing file is created. `HEAD <path>` answers the
  file's length in `Content-Length`, 404 for a missing file.
- Callers build the filer URL from `settings.seaweed_url`, see
  `routes/scenarios.py`.

## Scope

- `File.append()`: `POST` the URL with `op=append` in the query and
  `self.data` as the body; raise on an error status like `put`.
- `File.size() -> int`: `HEAD` the URL; the `Content-Length` of a 200, 0
  for a 404, raise on any other error status.
- `DiskFiler`: `POST` with `op=append` appends the body to the target,
  creating it and its directories as `_write` does; the file is created
  0600. `POST` without `op=append` stays 405. `HEAD` answers 200 with the
  file's length in `Content-Length` and no body, or 404.
- Nothing else changes; no caller uses `append` or `size` yet.

## Failure behaviour

- A path with an empty, `.` or `..` segment is refused as for every other
  method, before any request.
- An append to a path whose parent is a file fails and leaves both files as
  they were.
- A filer error surfaces as the `httpx` status error; `File` adds no retry.

## Tests

In `packages/api/tests/test_files.py`, against the disk filer in `tmp_path`:

- positive: two appends to a missing file leave exactly both bodies in
  order; an append after a `put` adds to what was put; a new file is 0600
  and its new directories 0700; `size` is the length after each step and 0
  for a missing file;
- negative: `POST` without `op=append` is 405; an append through a path
  with `..` raises before touching the disk; against an `httpx.MockTransport`
  standing in for the filer, the request is `POST` with `op=append` and the
  body unchanged, and a 500 raises from `append` and from `size`.

## Acceptance

`.venv/bin/pytest -q packages/api` green; `.venv/bin/pre-commit run
--files` on the edited files passes; `make smoke` still passes.
