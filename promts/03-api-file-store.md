# 03 - API: file store (SeaweedFS or a local fake)

Read `promts/00-README.md` first; its ground rules apply. Then read the
Storage section of `docs/project/05-recording.md` and the Stack section of
`docs/project/01-architecture.md`.

## Goal

One small module through which the API writes and reads files: scenario
bundles now, recordings from item 9. With `JATSZOTER_SEAWEED_URL` set it talks
to a SeaweedFS filer over HTTP; without it a fake with the same client API
writes to `data/storage/` under the working directory, the way fakeredis
stands in for Valkey. Where that directory lives is the environment's concern.

## Scope

- `settings.py`: `seaweed_url: AnyHttpUrl | None = None`; no setting for the
  local directory.
- `packages/api/src/jatszoter_api/files.py`:
  - `class Files` over one `httpx.Client` with `put(path: str, data: bytes)
    -> str` (returns the URL to store in a row), `get(url: str) -> bytes`,
    `delete(url: str) -> None`;
  - `get_files()` cached like `get_settings()`, picking the client lazily as
    `Cached._client` does; nothing is required at app start;
  - paths are relative, `/`-separated, and a segment `..`, `.` or empty is
    refused with `ValueError`; the URL is the base plus the quoted path;
  - SeaweedFS: PUT of the raw body to `<filer>/<path>`, GET and DELETE on the
    same URL; any status other than 2xx raises; timeouts 30 s;
  - fake: `DiskFiler`, an `httpx` transport answering the filer's PUT, GET
    and DELETE from `data/storage/`, based at `http://files.invalid/`;
    directories created 0700, files written 0600 through a temporary file
    and `os.replace`, a missing file answers 404;
  - `get` and `delete` accept only URLs `put` could have returned for the
    configured base and raise `ValueError` for any other, so a stored row can
    never make the API read an arbitrary path or host.
- `httpx` becomes a runtime dependency of `jatszoter-api` (it is already
  pinned in `requirements.txt`); `data/` is git-ignored.
- `tests/conftest.py`: the autouse `settings` fixture changes into
  `tmp_path`, unsets `JATSZOTER_SEAWEED_URL` and clears the `get_files` cache
  around each test.
- Tests in `packages/api/tests/test_files.py`.

## Tests

Positive: local put, get, delete round trip under `data/storage/` with the
right modes; put replaces an existing file; without a URL the fake is used;
the SeaweedFS client against `httpx.MockTransport` sends PUT, GET and DELETE
to the expected URLs.

Negative: `..`, absolute and empty paths refused; `get` and `delete` of a URL
outside the base refused for both; a non-2xx answer from the filer raises.

## Acceptance

`.venv/bin/pytest -q packages/api` green; pre-commit clean on every changed
file; `make smoke` still passes unchanged.
