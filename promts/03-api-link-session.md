# 03 - API: a link opens a session and attaches

Read `promts/00-README.md` first; its ground rules apply. Then read
Viewers, Links and Console store in `docs/project/04-console.md`, Link
threats and Web in `docs/project/06-security.md` and Ephemeral state in
`docs/project/02-domain-model.md`.

## Goal

`GET /s/{token}` turns a link into a cookie session, and that cookie
attaches to its deployment's console with what its user's role may see. A
revoked link loses its sockets at once. Viewers still cannot type.

## Established facts

- `routes/console.py` serves `WS /deployments/{id}/attach` for a signed
  operator: `_watching` checks the caller before `accept`, a refusal is
  close code 1008, `_drop_frames` discards what the viewer sends, and an
  idle viewer polls the deployment's status every second.
- `Console.ready()` is the READY offset or `None`; `tail()` and
  `since(offset)` read the Valkey tail; `held(filer_url)` is the log end.
- `ConsoleHub.viewer(deployment_id)` registers a `Viewer` with `put`,
  `take` and `wake`; sockets may live in different event loops, so the hub
  reaches each through `call_soon_threadsafe`.
- `Cached` gives `_get`, `_set`, `_ttl` and `_delete` on a full key; `Base`
  overrides them per row id, and a model reaches the plain ones as
  `Cached._get(self, key)`.
- `models/user.py` imports `Link`, so `models/link.py` cannot import
  `User`.
- `create_app` mounts every router under `/api/v1`.
- uvicorn's access log writes each request's path.

## Scope

- `models/link.py`:
  - `open()` stamps `opened_at` on the first call, saves, mints a session
    token of 32 random bytes and stores `session:<sha256>` with the link id
    for one day; it returns the token;
  - `resume(token)` loads the link a session token names, renewing the
    day; a miss is `IndexError`, like `load`;
  - `live` is true while `revoked_at` is unset.
- `routes/links.py`: `GET /s/{token}` on a second router mounted outside
  `/api/v1`. It loads the link by the token's sha256 and compares the hash
  in constant time. A link that is unknown or not live, or whose user is
  not `enabled`, is 404 with one body. Otherwise it opens the link and
  answers 303 to `/t/{deployment_id}` with `Set-Cookie: jatszoter_link`,
  HttpOnly, Secure, SameSite=Strict, `Path=/api/v1/deployments/{id}`, no
  expiry, plus `Referrer-Policy: no-referrer` and `Cache-Control:
  no-store`.
- `app.py`: a filter on the `uvicorn.access` logger rewrites `/s/<token>`
  to `/s/<redacted>`.
- `auth.py`: `socket_link(websocket, session, deployment_id)` returns the
  link and its user from the cookie. It refuses a missing or unknown
  session, a link that is not live or names another deployment, a user
  that is not `enabled`, and an `Origin` header whose host is not the
  upgrade's `Host`.
- `console_hub.py`: `viewer` takes an optional link id; `revoke(link_id)`
  marks that link's viewers and wakes them.
- `routes/console.py`:
  - `attach` takes a signed operator when the upgrade carries
    `X-Signature`, otherwise a link; a link also needs the deployment
    READY;
  - a trainee or viewer link starts at the READY offset: `offset` below it
    is raised to it, and without a READY offset the view starts at the log
    end; an operator link reads like a signed operator;
  - a viewer the hub marked revoked is closed with 4403.
- `routes/links.py`: `revoke` also tells the hub.

## Failure behaviour

- Every refusal before `accept` is 1008, whatever the reason.
- A session whose link was revoked, or whose deployment ended, opens
  nothing; the Valkey key is left to expire.
- Neither the link token nor the session token reaches a log line or an
  error message.

## Tests

- `tests/test_links_api.py`:
  - positive: open answers 303, the location has no token, the cookie
    carries every attribute and the path of its deployment; `opened_at` is
    stamped once across two opens; the access-log filter redacts the path;
  - negative: an unknown token, a revoked link and a disabled user get the
    same 404 body and no cookie.
- `tests/test_console_api.py`:
  - positive: a trainee link attaches at READY and its first offset is the
    READY offset, with bytes before it never sent; `offset` below READY is
    raised; an operator link gets the tail from its start; revoking a link
    closes its socket with 4403 and leaves another link's socket open;
  - negative: a link is refused before READY, on another deployment, after
    revoke, with a foreign `Origin`, and without a cookie; a terminal
    deployment still closes with 1000.

## Acceptance

`.venv/bin/pytest -q packages/api` green; `make smoke` passes;
`.venv/bin/pre-commit run --files` on every edited file passes.
