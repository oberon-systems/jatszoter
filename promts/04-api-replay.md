# 04 - API: list recordings and download one

Read `promts/00-README.md` first; its ground rules apply. Then read Replay
in `docs/project/05-recording.md` and Operator API in
`docs/project/01-architecture.md`.

## Goal

A signed operator finds the recordings of a deployment and downloads any
of them, while it is being written or after the deployment is gone.

## Established facts

- `GET /deployments/{id}/console` in `routes/deployments.py` reads a file
  through `File.get()` and answers an empty body when the filer has none.
- `routes/links.py` shows a router whose every route demands a signed
  operator.
- A Recording row has `deployment_id`, `link_id`, `size_bytes` and
  `duration_seconds`; the last two are 0 until it is closed.

## Scope

- `routes/deployments.py`: `GET /deployments/{id}/recordings` answers the
  deployment's recordings, oldest first: `id`, `link_id`, `created_at`,
  `size_bytes`, `duration_seconds`. Never the URL.
- `routes/recordings.py`, new, registered in `routes/__init__.py` and
  `app.py`: `GET /recordings/{recording_id}.cast` answers the file as
  `application/x-asciicast` with `Cache-Control: no-store`.
- Both routes take a signed operator request and nothing else.

## Failure behaviour

- An unknown recording, an id that is not a UUID, and a row whose file is
  gone all answer 404.
- A link cookie, of any role, gets what it gets on every other operator
  route.

## Tests

`tests/test_recordings_api.py`:

- positive: the listing shows the recording of an opened trainee link with
  zero size while live and the stored size after a revoke; the download of
  a live recording starts with the header; the download after the
  deployment completed returns the same bytes as the file;
- negative: both routes refuse an unsigned request and a trainee, viewer
  and operator link cookie; an unknown id is 404; the listing of an
  unknown deployment is 404; a deployment whose link was never opened
  lists nothing.

## Acceptance

`.venv/bin/pytest -q packages/api` green; `make smoke` passes;
`.venv/bin/pre-commit run --files` on every edited file passes.
