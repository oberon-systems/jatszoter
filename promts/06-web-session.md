# 06 - Web: session, signed client, CSRF, relay and shell

Read `promts/00-README.md` first; its ground rules apply. Then read
`docs/project/08-web-ui.md`, `docs/project/06-security.md`,
`docs/project/04-console.md` and the `Design system` page in
`dev/web/templates/base`.

## Goal

`web` is a working frame for every later screen: an operator logs in and
out, each API call is signed by `web`, state-changing requests carry a
CSRF token, link traffic is relayed, and the shell renders.

## Established facts

- `packages/web` is a FastAPI app with `/healthz` and nothing else.
- `POST /api/v1/auth/login` answers the user id, the credential and the
  key; a first login of `admin` is refused until it sends `new_password`.
- A credential lives an hour and is due for refresh 15 minutes before it
  expires; `POST /api/v1/auth/credential` refreshes it.
- A signature is the hex HMAC-SHA256 of timestamp, method, path and the
  sha256 of the body, one per line.

## Scope

`packages/web` and its tests; `requirements.txt` and
`.pre-commit-config.yaml` for what the user agreed to add.

- Settings read once in `create_app(settings)`: the API URL, the cookie
  key and what `08-web-ui.md` names.
- The API client: signs with the session's credential, refreshes it when
  due, and maps an API refusal to the page `08-web-ui.md` describes.
- Login, the forced password change of a first login, logout.
- The session cookie as `08-web-ui.md` specifies it; a tampered or
  expired one is a logged-out browser, never an error page.
- CSRF as specified, on every state-changing route of `web`.
- The relay of the link paths and of the terminal socket, close codes
  passed through.
- Security headers on every answer.
- The shell from the `Design system` boards: base template, navigation,
  flash messages, the vendored HTMX and the stylesheet.

## Failure behaviour

An API that is down shows the error state of the board, with no trace and
no URL of the API. A missing or wrong CSRF token answers 403 and changes
nothing. No password, key, cookie value or token reaches a log or a page.

## Acceptance

- `.venv/bin/pytest -q packages/web` passes: login, the forced password
  change, logout, refresh, a tampered cookie, CSRF refusals, the relay of
  `/s/{token}` and of a link request, the headers.
- No answer of `web` carries the credential key.
- `make smoke` passes.
- `.venv/bin/pre-commit run --files <paths>` passes.
