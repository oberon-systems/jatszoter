# 04 - API: take control, keys and resize

Read `promts/00-README.md` first; its ground rules apply. Then read
Pipeline, Viewers, Take control and Resize in `docs/project/04-console.md`
and Web in `docs/project/06-security.md`.

## Goal

The API knows who holds the keyboard, lets an operator change it, and
passes keys and resizes from the holder's sockets down the agent's console
socket. Nobody else's input leaves the API.

## Established facts

- `models/console.py` keeps `console:<id>:{tail,start,end,ready}` with a
  one-day TTL that `append` renews, and `drop()` deletes them.
- `ConsoleHub.agent(deployment_id)` holds the agent socket and yields the
  event a newer socket sets; `_next` in `routes/console.py` waits for a
  frame or that event.
- After prompt 03 an attached socket is a signed operator or a link with a
  role, and `_drop_frames` still discards what it sends.
- The agent reads a text frame as JSON and ignores fields it does not
  know; it does nothing with a binary frame yet.
- The deployments router demands a signed operator on every route, so a
  route a cookie may call lives on the console router.

## Scope

- `models/console.py`: `Holder` (`trainee`, `operator`); `control()` reads
  the holder, `trainee` when the key is absent; `give(holder)` writes
  `console:<id>:control` with the TTL; `append` renews it and `drop()`
  deletes it.
- `auth.py`: a dependency for routes an operator reaches by signature or
  by link: a signed operator, or the cookie of a live operator link of
  this deployment whose `Origin`, when sent, names the request's host.
  Anyone else is 401 without a credential and 403 with a wrong one.
- `console_hub.py`:
  - the agent entry gains a queue of at most 256 messages;
    `to_agent(deployment_id, message)` puts `bytes` or `str` there and
    answers whether it was taken;
  - `announce(deployment_id, holder)` hands the holder to every viewer of
    the deployment and wakes it.
- `routes/console.py`:
  - `POST /deployments/{id}/control` with `{"holder": ...}`: 409 unless the
    deployment is READY; stores the holder, announces it and answers it;
  - `ship` sends what waits in the queue between frames: `bytes` as a
    binary frame, `str` as text;
  - `attach` sends `{"control": holder}` after its first offset and on
    every announcement;
  - the reader that replaces `_drop_frames`: a binary frame over 4096
    bytes closes the socket with 1009; a text frame must be an object with
    integer `cols` and `rows` from 1 to 500 and is dropped otherwise. Each
    accepted frame is forwarded only when the socket's role equals
    `control()`, read for that frame; a viewer link never forwards.

## Failure behaviour

- Without an agent socket, or with its queue full, the frame is dropped
  and the viewer's socket stays open.
- A lost Valkey gives control back to the trainee, the default.
- Keys are never stored and never logged, not their bytes and not their
  length.

## Tests

- `tests/test_console.py`: the holder defaults to `trainee`, survives an
  append, and is gone after `drop()`.
- `tests/test_console_api.py`, with a fake agent socket attached:
  - positive: a trainee link's keys reach the agent as one binary frame;
    after the operator takes control the trainee's keys stop and a signed
    operator's arrive; giving control back reverses it; an operator link
    may set the holder; every viewer receives `{"control": ...}` on attach
    and on a change; the holder's resize reaches the agent as text;
  - negative: a viewer link's keys and resizes never arrive; a resize from
    the party without control never arrives; `cols` 0, `rows` 501 and a
    non-object are dropped; a 4097-byte frame closes with 1009; control is
    409 before READY, 403 for a trainee or viewer link, and refused with a
    foreign `Origin`; keys with no agent attached keep the viewer open.

## Acceptance

`.venv/bin/pytest -q packages/api` green; `make smoke` passes;
`.venv/bin/pre-commit run --files` on every edited file passes.
