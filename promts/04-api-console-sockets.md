# 04 - API: console sockets, hub and download

Read `promts/00-README.md` first; its ground rules apply. Then read
`docs/project/04-console.md`, Agent protocol in
`docs/project/01-architecture.md`, Credentials in
`docs/project/06-security.md`, `packages/api/src/jatszoter_api/auth.py`,
`routes/agents.py`, `routes/deployments.py` and `models/console.py` from
prompt 03.

## Goal

The agent's console socket feeds the store and an in-process hub; an
operator attaches to a deployment's console and sees the tail, then every
new byte; a slow viewer never grows API memory past its cap; an operator
downloads the full log.

## Established facts

- `require_agent` and `require_user` take a `Request` and its body. A
  WebSocket route has neither, so its signature is checked from the
  upgrade's headers over method `GET`, the URL path and an empty body,
  through the same `_signed`.
- The deployments router is guarded by `require_operator` for every route;
  a WebSocket route needs its own check, not the router dependency.
- `requirements.txt` pins `uvicorn` without `websockets`, so `make run-api`
  cannot serve a WebSocket yet. Starlette's `TestClient` speaks WebSocket
  without that package.
- The store calls are synchronous (SQL session, filer, Valkey): from an
  async route they run in a worker thread so the event loop never blocks.
- naos keeps its hub on `app.state` and registers each socket with the loop
  it runs in, because `TestClient` runs every socket in its own loop:
  `naos/packages/api/src/naos_api/console_hub.py` is the model.

## Scope

- `requirements.txt`: `websockets`, pinned to its current release.
- `settings.py`: `console_viewer_buffer_bytes` (4 MiB), env
  `JATSZOTER_CONSOLE_VIEWER_BUFFER_BYTES`.
- `console_hub.py`: `ConsoleHub` on `app.state`, built in `create_app`.
  One agent socket per deployment, a newer one replacing and closing the
  older; a set of viewers per deployment, each with a queue counted in
  bytes. `publish(deployment_id, offset, data)` puts into every viewer's
  queue; a queue that would pass the cap is emptied and marked behind at
  the last offset it was sent.
- `WS /api/v1/agents/{agent_id}/deployments/{id}/console`:
  - refused before accept when the signature fails, the deployment is not
    the agent's, the agent has no live lease, or the deployment is not
    STARTING, BOOTSTRAPPING or READY;
  - on accept sends `{"offset": held}`; each binary frame is an 8-byte
    big-endian offset and at most 64 KiB, appended through
    `Console.append`, published, and answered `{"offset": end}`;
  - text frames from the agent are ignored; a frame over the size closes
    with 1009; `TooLargeError` closes with 4413.
- `WS /api/v1/deployments/{id}/attach`:
  - signed operator only, checked from the upgrade's headers; anything else
    refused before accept; an unknown deployment refused the same way;
  - optional query `offset`: when `Console.since(offset)` has it, the
    viewer resumes there, otherwise it gets `Console.tail()`;
  - a text frame `{"offset": n}` comes before the first bytes and before any
    jump, then raw console bytes as binary frames;
  - a viewer marked behind is resent from `since(last offset)`, or closed
    with 4429 when that is gone;
  - frames from the viewer are read and dropped (item 8 gives them meaning);
    the socket closes with 1000 once the deployment is terminal.
- `GET /api/v1/deployments/{id}/console`: the full log as
  `application/octet-stream`, operator only like the router's other routes;
  an empty body when no log exists yet.

## Failure behaviour

- An agent socket that drops leaves the hub without a writer; viewers stay
  attached and idle until a new agent socket publishes again.
- A store error on a frame closes the agent socket with 1011 and logs the
  deployment id and the error type, never the bytes; the agent reconnects
  and resends from `held`.
- Closing a viewer never touches the agent socket or other viewers.
- No console byte reaches a log line or an error response.

## Tests

`packages/api/tests/test_console_api.py` with `TestClient`, the disk filer
in `tmp_path` and the fake cache:

- positive: an agent frame is answered with the new end and lands in the
  log; an attached operator receives `{"offset": ...}` then the published
  bytes; a viewer attaching later gets the tail; a viewer with `offset`
  inside the tail resumes there; a second agent socket replaces the first;
  the download returns the full log;
- negative: an unsigned or wrongly signed agent socket, another agent's
  deployment, a dead lease and a PENDING deployment are refused; a trainee
  or unsigned viewer is refused; a frame shorter than 8 bytes or over the
  size closes with 1009; a full log closes with 4413; a viewer that never
  reads stays under its cap and is resent or closed with 4429; the download
  of an unknown deployment is 404 and of a trainee 403.

## Acceptance

`.venv/bin/pytest -q packages/api` green; `.venv/bin/pre-commit run
--files` on the edited files passes; `make install` picks up `websockets`;
`make smoke` still passes.
