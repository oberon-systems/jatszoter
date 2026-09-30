# 03 - API: file store (SeaweedFS or a local directory)

Read `promts/00-README.md` first; its ground rules apply. Then read the
Storage section of `docs/project/05-recording.md` and the Stack section of
`docs/project/01-architecture.md`.

## Goal

One small module through which the API writes and reads files: scenario
bundles now, recordings from item 9. With `JATSZOTER_SEAWEED_URL` set it talks
to a SeaweedFS filer over HTTP; without it, it writes to the local directory
`JATSZOTER_FILES_DIR`.

## Scope

- `settings.py`: `seaweed_url: AnyHttpUrl | None = None`,
  `files_dir: Path | None = None`.
- `packages/api/src/jatszoter_api/files.py`:
  - `class Files` with `put(path: str, data: bytes) -> str` (returns the URL
    to store in a row), `get(url: str) -> bytes`, `delete(url: str) -> None`;
  - `get_files()` cached like `get_settings()`, picking the backend; neither
    setting present raises a clear error at app start (`create_app` calls
    it), naming both variables;
  - paths are relative, `/`-separated, and a segment `..`, `.` or empty is
    refused with `ValueError`;
  - SeaweedFS backend: `httpx` PUT of the raw body to `<filer>/<path>`, GET
    and DELETE on the same URL; the URL returned is that one; any status other
    than 2xx raises; timeouts 30 s;
  - local backend: files under `files_dir`, directories created 0700, files
    written 0600 through a temporary file and `os.replace`; the URL is
    `file://` plus the absolute path;
  - `get` and `delete` accept only URLs under the configured backend's base
    and raise `ValueError` for any other, so a stored row can never make the
    API read an arbitrary path or host.
- `httpx` becomes a runtime dependency of `jatszoter-api` (it is already
  pinned in `requirements.txt`).
- `tests/conftest.py`: the autouse `settings` fixture sets
  `JATSZOTER_FILES_DIR` to a `tmp_path` directory and clears the `get_files`
  cache around each test.
- Tests in `packages/api/tests/test_files.py`.

## Tests

Positive: local put, get, delete round trip with the right modes; put
replaces an existing file; SeaweedFS backend against `httpx.MockTransport`
sends PUT, GET and DELETE to the expected URLs.

Negative: `..`, absolute and empty paths refused; `get` of a URL outside the
base refused for both backends; a 500 from the filer raises; no settings at
all fails `create_app` with the message naming both variables.

## Acceptance

`.venv/bin/pytest -q packages/api` green; pre-commit clean on every changed
file; `make smoke` still passes once it sets `JATSZOTER_FILES_DIR` - add
that one line to `make/tests/smoke.sh` in this prompt.
