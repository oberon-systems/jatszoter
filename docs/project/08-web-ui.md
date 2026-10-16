# 08 - Web UI

`web` is the one origin a browser talks to. It serves the operator pages
and the trainee page, signs every operator call to the API itself, and
relays a link's traffic to the API unchanged. This document is the contract
every board and every page of the Web UI is built from.

- [Principles](#principles)
- [Settings](#settings)
- [Routes](#routes)
- [Screens](#screens)
- [API gaps](#api-gaps)
- [Operator session](#operator-session)
- [Relay](#relay)
- [Terminal sockets](#terminal-sockets)
- [CSRF](#csrf)
- [Security headers](#security-headers)
- [Acceptance criteria](#acceptance-criteria)

## Principles

- A browser talks to `web` alone. `web` talks to the API at
  `JATSZOTER_WEB_API_URL`, and no reverse proxy is needed on any stand.
- `web` keeps no store and reads no database. Everything it shows comes
  from the API; the operator's credential lives in an encrypted cookie.
- The UI is never an authorization boundary: the API authorizes every
  action, and a dangerous one - stop, revoke, disable, delete - asks for
  confirmation first.
- Every screen is drawn in Penpot and approved before it is built, see
  `dev/web/README.md`. A screen without an approved board gets nothing
  built.
- Pages are Jinja2 and HTMX; the terminal is
  [xterm.js](https://xtermjs.org/), the replay
  [asciinema-player](https://github.com/asciinema/asciinema-player). Both
  are vendored with their version and licence; no file is loaded from
  another host.

## Settings

Read once in `create_app(settings)` and passed on, with the `JATSZOTER_`
prefix like every other setting.

| Setting                     | Meaning                                        |
| --------------------------- | ---------------------------------------------- |
| `JATSZOTER_WEB_API_URL`     | base URL of the API, without `/api/v1`         |
| `JATSZOTER_WEB_COOKIE_KEYS` | JSON map of kid to a 32-byte base64url AES key |
| `JATSZOTER_WEB_COOKIE_KID`  | the kid of the key that encrypts new cookies   |

## Routes

| Path                                               | Method   | Who      | What                          |
| -------------------------------------------------- | -------- | -------- | ----------------------------- |
| `/login`                                           | GET POST | anyone   | log in, change password       |
| `/logout`                                          | POST     | operator | end the session               |
| `/`                                                | GET      | operator | 303 to `/deployments`         |
| `/deployments`                                     | GET      | operator | deployments list              |
| `/deployments/new`                                 | GET POST | operator | create a deployment           |
| `/deployments/{id}`                                | GET      | operator | deployment detail             |
| `/deployments/{id}/{action}`                       | GET POST | operator | stop, control, links, check   |
| `/deployments/{id}/attach`                         | WS       | operator | the signed terminal           |
| `/deployments/{id}/console.log`                    | GET      | operator | the full console log          |
| `/deployments/{id}/recordings/{recording_id}`      | GET      | operator | replay page                   |
| `/deployments/{id}/recordings/{recording_id}.cast` | GET      | operator | the asciicast file            |
| `/links/{id}/revoke`                               | GET POST | operator | revoke a link                 |
| `/scenarios`, `/images`                            | GET POST | operator | list and register             |
| `/scenarios/new`, `/images/new`, `/users/new`      | GET      | operator | the register and create forms |
| `/agents`, `/agents/{id}/state`                    | GET POST | operator | list, approve, enable         |
| `/users`, `/users/{id}/{action}`                   | GET POST | operator | list, create, state, password |
| `/logs`, `/logs/{deployment_id}`                   | GET      | operator | console logs                  |
| `/storage`, `/storage/{deployment_id}/purge`       | GET POST | operator | usage and purge               |
| `/results`                                         | GET      | operator | results table                 |
| `/t/{deployment_id}`                               | GET      | anyone   | trainee page                  |
| `/s/{token}`                                       | GET      | relay    | opens a link                  |
| `/api/v1/deployments/{id}/...`                     | GET POST | relay    | a link's API calls            |
| `/api/v1/deployments/{id}/attach`                  | WS       | relay    | a link's terminal             |
| `/static/...`                                      | GET      | anyone   | vendored and own files        |
| `/healthz`                                         | GET      | anyone   | liveness                      |

`relay` paths are passed to the API as described in [Relay](#relay); any
other path under `/api/v1` answers 404 from `web` and never reaches the
API.

A `GET` on an operator route that also takes `POST` answers the dialog that
posts it: the confirmation of a dangerous action, or a form. HTMX opens it
over the page.

## Screens

Every operator screen sits in the shell: navigation to every screen below,
the operator's name and a logout button. API calls are signed by `web`
unless a screen says otherwise. `Missing API` names the routes of
[API gaps](#api-gaps) the screen needs.

### Login

- Route: `/login`. Who: anyone.
- API calls: `POST /api/v1/auth/login`, then `GET /api/v1/auth/me`.
- Actions: log in with name and password. When the API answers 403
  `password change required`, the page asks for a new password twice and
  sends it as `new_password`.
- Answers: 401 shows `wrong name or password`, 429 shows `too many
  attempts, try again later`. A user whose role is not `operator` is
  logged out at once and told the account is not an operator's.
- Missing API: `GET /api/v1/auth/me`.

### Deployments list

- Route: `/deployments`. Who: operator.
- API calls: `GET /api/v1/deployments`, `GET /api/v1/scenarios`,
  `GET /api/v1/users`, `GET /api/v1/agents`.
- Shows: status, scenario title and version, trainee, agent, created and
  ended times, newest first, filtered by status. The list refreshes by
  HTMX polling every 5 seconds.
- Actions: open a deployment, create one, stop one.
- Missing API: `trainee_id` in the deployment answer, `GET /api/v1/agents`.

### Deployment create

- Route: `/deployments/new`. Who: operator.
- API calls: `GET /api/v1/scenarios`, `GET /api/v1/users?role=trainee`,
  `GET /api/v1/agents`, `POST /api/v1/deployments`.
- Actions: pick a scenario, a trainee and, optionally, an enabled agent to
  pin the deployment to.
- The answer page shows the trainee link once, with `Cache-Control:
  no-store`, and a button that opens the deployment detail. The link is
  never shown again; a lost one is revoked and issued anew.
- Missing API: `GET /api/v1/agents`.

### Deployment detail

- Route: `/deployments/{id}`. Who: operator.
- API calls: `GET /api/v1/deployments/{id}`, `.../links`, `.../checks`,
  `.../recordings`, `GET /api/v1/users`, `GET /api/v1/agents`, the
  signed terminal of [Terminal sockets](#terminal-sockets).
- Shows: status and reason, scenario, agent, resources, timeouts, the
  terminal with who holds control, links with role and state, checks with
  `detail`, recordings with size and duration. Status, links and checks
  refresh by HTMX polling every 5 seconds.
- Actions: take and give back control (`POST .../control`), issue a link
  to an enabled user (`POST .../links`, the link shown once), revoke a
  link, request a check, stop, download the console log, open a
  recording's replay.
- Missing API: `GET /api/v1/agents`.

### Scenarios and images

- Routes: `/scenarios` and `/images`, two tabs of one screen. Who:
  operator.
- API calls: `GET` and `POST /api/v1/scenarios`, `GET` and
  `POST /api/v1/images`.
- Shows: scenarios by slug, version, title and image; images by name, URL
  and digest.
- Actions: register a scenario from a bundle upload or a URL; register an
  image with name, URL and digest. A refused registration shows the API's
  message.
- Missing API: none.

### Agents

- Route: `/agents`. Who: operator.
- API calls: `GET /api/v1/agents`, `POST /api/v1/agents/{agent_id}/state`.
- Shows: pending registrations, then agents with name, labels, state,
  last heartbeat, capacity and lease deadline.
- Actions: approve a registration, enable or disable an agent. Disabling
  asks for confirmation: the agent's lease lapses and its deployments
  fail.
- Missing API: both routes.

### Users

- Route: `/users`. Who: operator.
- API calls: `GET` and `POST /api/v1/users`,
  `POST /api/v1/users/{id}/state`, `POST /api/v1/users/{id}/password`.
- Shows: name, role, comment, state and creation time, filtered by role.
- Actions: create a user; enable or disable one, with confirmation; set a
  one-time password, which the user must change at the first login.
- Missing API: the state and password routes.

### Logs

- Routes: `/logs` and `/logs/{deployment_id}`. Who: operator.
- API calls: `GET /api/v1/deployments`, `GET /api/v1/scenarios`,
  `GET /api/v1/deployments/{id}/console` through
  `/deployments/{id}/console.log`.
- Shows: the deployments, each with a link to its console log. The log
  page draws the file in a read-only xterm.js, so escape sequences render
  as the terminal showed them, and offers it as a download.
- Missing API: none.

### Storage management

- Routes: `/storage` and `/storage/{deployment_id}/purge`. Who: operator.
- API calls: `GET /api/v1/storage`,
  `POST /api/v1/deployments/{id}/purge`.
- Shows: per deployment the bytes of its console log and of its
  recordings, the number of recordings, and the totals with the scenario
  bundles.
- Actions: purge a COMPLETED, FAILED or CANCELLED deployment, with
  confirmation: its console log and its recordings are deleted, files and
  rows; the deployment and its checks stay.
- Missing API: both routes.

### Results

- Route: `/results`. Who: operator.
- API calls: `GET /api/v1/checks`, `GET /api/v1/deployments`,
  `GET /api/v1/scenarios`, `GET /api/v1/users`.
- Shows: one row per deployment that has a check: scenario, trainee,
  deployment status, the last check's status and message, the number of
  checks and a link to the deployment detail. Filters by scenario and by
  trainee.
- Missing API: `GET /api/v1/checks`, `trainee_id` in the deployment
  answer.

### Trainee page

- Route: `/t/{deployment_id}`, where `GET /s/{token}` sends a link's
  holder. Who: anyone; the page holds nothing until a link session
  answers.
- API calls, all through the [relay](#relay) with the link cookie:
  `GET /api/v1/deployments/{id}/task`, the attach socket, `GET` and
  `POST .../checks`, and `POST .../control` for an operator link.
- Shows: scenario title, the task text as preformatted text, status, the
  time left in the session, the terminal with who holds control, and the
  checks without `detail`. Until READY the page says the VM is being
  prepared and polls the task every 5 seconds.
- Actions by role: a trainee types while it holds control and requests a
  check; an operator link also takes and gives back control; a viewer
  only watches.
- A 401 or 403 from the task route shows `this link is not valid or has
  ended` and nothing else.
- The page is driven by a first-party static script over the relayed JSON
  routes, since `web` never sees the link cookie.
- Missing API: `GET /api/v1/deployments/{id}/task`.

### Replay page

- Route: `/deployments/{id}/recordings/{recording_id}`. Who: operator.
- API calls: `GET /api/v1/deployments/{id}/recordings`,
  `GET /api/v1/recordings/{recording_id}.cast` through
  `/deployments/{id}/recordings/{recording_id}.cast`.
- Shows: the recording in a vendored asciinema-player, and the `m` events
  of the file as jump points: their time and label, nothing else from the
  file. A click seeks the player to that time.
- A recording not in the deployment's list answers 404.
- Missing API: none.

## API gaps

What the screens need and the API lacks. Every route is signed and
answers JSON unless it says otherwise.

| Route or change                        | Who            | What it does                                          |
| -------------------------------------- | -------------- | ----------------------------------------------------- |
| `GET /api/v1/auth/me`                  | signed user    | the user's id, name and role                          |
| `GET /api/v1/agents`                   | operator       | agents with state, labels and lease; registrations    |
| `POST /api/v1/agents/{agent_id}/state` | operator       | approve, enable or disable an agent                   |
| `POST /api/v1/users/{id}/state`        | operator       | enable or disable a user                              |
| `POST /api/v1/users/{id}/password`     | operator       | set a one-time password                               |
| `trainee_id` in deployment answers     | operator       | the trainee the deployment was created for            |
| `GET /api/v1/deployments/{id}/task`    | operator, link | status, title, task text, role, session deadline      |
| `GET /api/v1/checks`                   | operator       | every check, newest first, with its deployment        |
| `GET /api/v1/storage`                  | operator       | bytes held per deployment and in scenario bundles     |
| `POST /api/v1/deployments/{id}/purge`  | operator       | delete a terminal deployment's console and recordings |
| superseded user credential             | -              | answers 401 and disables nobody                       |

- `GET /api/v1/agents` lists a pending registration from Valkey with its
  id, name, labels and registration time, and a stored agent with its
  last heartbeat, capacity and lease deadline when a lease is live.
- `POST /api/v1/agents/{agent_id}/state` takes `{"state": "enabled" |
  "disabled"}`; `enabled` on a pending registration approves it.
- `POST /api/v1/users/{id}/state` takes the same body. Disabling the last
  enabled operator answers 409. Disabling a user closes every socket of
  their links with 4403.
- `POST /api/v1/users/{id}/password` takes `{"password": "..."}` and sets
  `password_changed` to 0, so the next login demands a new password.
- `GET /api/v1/deployments/{id}/task` answers `status`, `title`,
  `description` (the Markdown text of the scenario's `description.md`),
  `role` (the link user's role, `operator` for a signed request) and
  `session_ends_at` (null before READY).
- `POST /api/v1/deployments/{id}/purge` answers 409 unless the deployment
  is COMPLETED, FAILED or CANCELLED. A purged deployment counts 0 bytes in
  `GET /api/v1/storage`.
- A user's superseded generation answers 401 and disables nobody, see
  [06](06-security.md#credentials); an agent's still disables it.

## Operator session

An operator logs in on `/login`; `web` keeps the credential and its key in
the `jatszoter_session` cookie and signs every API call with them. The key
never reaches JavaScript.

- The cookie is HttpOnly, Secure, SameSite=Strict, `Path=/`, without an
  expiry. Its value is `<kid>.<base64url of nonce and ciphertext>`:
  AES-256-GCM from [cryptography](https://cryptography.io/), a 12-byte
  random nonce per write, the cookie name as associated data.
- The plain text is JSON: user id, name, credential, `expires_at`, key and
  the CSRF token. An unknown kid or a value that does not decrypt is no
  session. A retired kid stays in `JATSZOTER_WEB_COOKIE_KEYS` for at least
  an hour, the life of a credential.
- Refresh: a request whose credential is due, 15 minutes or less before
  `expires_at`, first calls `POST /api/v1/auth/credential` and answers
  with the new cookie.
- Lapsed: a credential past `expires_at` is never sent. `web` clears the
  cookie and answers a page with 303 to `/login?next=<path>`, an HTMX
  request with `HX-Redirect` to the same. `next` is kept only when it is a
  local path.
- Refused: a 401 from the API answers an HTMX request with `HX-Refresh`,
  since another tab may have refreshed the cookie meanwhile, and a page
  like a lapsed credential. A 403 shows a page that says the action is not
  allowed.
- Flash: the message a redirect leaves for the next page rides in the
  `jatszoter_flash` cookie, sealed like the session cookie with its own name
  as associated data and with the same attributes. The next full page shows
  it once and clears it; it carries text only, never a token or a link.
- Logout: `POST /logout` calls `POST /api/v1/auth/logout`, clears the
  cookie whatever the API answered, and redirects to `/login`.
- A second login, in another browser, supersedes the first; the first gets
  401 at its next call and lands on the login page.

## Relay

`web` passes a link's traffic to the API unchanged and never signs it.

- Paths: `GET /s/{token}`, `GET` and `POST` under
  `/api/v1/deployments/{id}/`, and the attach socket. Nothing else under
  `/api/v1` is relayed.
- Passed on unchanged: method, path, query and a body of at most 64 KiB;
  a larger one answers 413. The answer comes back with its status, body,
  `Content-Type`, `Location`, `Set-Cookie`, `Cache-Control` and
  `Referrer-Policy`.
- Request headers are an allow-list: `Host` and `Origin` unchanged, so the
  API's `Origin` check compares the browser's own pair, `Content-Type`,
  `Accept`, and `Cookie` carrying `jatszoter_link` alone.
  `jatszoter_session`, `jatszoter_csrf` and any `X-Owner`, `X-Credential`,
  `X-Timestamp` or `X-Signature` never reach the API through the relay.
- An API that cannot be reached answers 502, one that does not answer
  within 30 seconds 504.
- The access log of `web` shows method, path, status and duration. A link
  open is written as `/s/<redacted>`; no header, cookie or body is logged.

## Terminal sockets

A browser opens one of two sockets, and `web` opens the API's attach socket
behind it.

- A link's terminal is `WS /api/v1/deployments/{id}/attach`, relayed like
  any relay path: `Host`, `Origin`, the `offset` query and the link cookie
  reach the API unchanged.
- The operator's terminal is `WS /deployments/{id}/attach`. `web` checks
  the session cookie and the `Origin`, which must name its own host, then
  opens the API's attach socket signed with the operator's credential.
- `web` opens the API's socket first and accepts the browser's only once
  it is open. Frames pass both ways unchanged, text as text and binary as
  binary, one at a time: a slow side holds the other back, and nothing is
  buffered beyond one frame.
- Close codes:

  | Case                                                | Browser gets      |
  | --------------------------------------------------- | ----------------- |
  | the API closes with a code                          | the same code     |
  | the API refuses the upgrade                         | 1008              |
  | `web` refuses: no session, lapsed, foreign `Origin` | 1008              |
  | the API cannot be reached or drops without a close  | 1011              |
  | the browser closes                                  | API socket closed |

- The page reconnects after 4429 and 1011 with a pause, and never after
  1000, 1008 or 4403.

## CSRF

`web` checks the CSRF token of every state-changing request, for an
operator session and for a link alike, before anything reaches the API.
The API keeps its own `Origin` check and SameSite=Strict on the link
cookie.

- Operator: the token is 32 random bytes, base64url, made at login and
  kept in the encrypted session cookie. A page carries it in
  `<meta name="csrf-token">` and in a hidden `csrf_token` field of every
  form; HTMX sends it as `X-CSRF-Token` through `hx-headers` on `<body>`.
- Link and login: a page that finds no `jatszoter_csrf` cookie sets one,
  32 random bytes, HttpOnly, Secure, SameSite=Strict, `Path=/`, and
  carries the same value in the page. The trainee page's script sends it
  as `X-CSRF-Token`; the relay compares it with the cookie.
- Every `POST` to `web` and every relayed `POST` whose token is missing or
  does not match, compared in constant time, answers 403. So does one
  whose `Origin`, when sent, does not name `web`'s own host.
- `GET` changes nothing and carries no token. A socket upgrade carries
  none either; the `Origin` check guards it, in `web` for the operator and
  in the API for a link.

## Security headers

Every answer of `web`, relayed ones included, carries:

```text
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'
Referrer-Policy: no-referrer
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
```

- No page loads a script, a style, a font or an image from another host;
  every third-party file is vendored under the package's static directory.
- No inline script or style and no `hx-on` attribute: HTMX is configured
  through `<meta name="htmx-config">` with `includeIndicatorStyles` off.
- The `<style>` elements the xterm.js DOM renderer injects are refused by
  the policy, so `static/terminal.css` carries them for the one theme the
  terminal uses. A 24-bit colour, which xterm.js writes as a `style`
  attribute, falls back to the default colour.
- Every page that shows data from the API is `Cache-Control: no-store`.
- `web` is served over HTTPS, which the Secure cookies need; plain HTTP is
  for loopback alone.

## Acceptance criteria

- Every screen of [Screens](#screens) has an approved board before its
  page exists, and every page matches its board.
- A request without a session cookie, with a cookie that does not decrypt
  or with a lapsed credential reaches no API route and lands on `/login`.
- The session cookie never holds the key in plain text, and no page,
  script or log carries the key, the credential, a token or a cookie
  value.
- A `POST` to `web` or through the relay without the right CSRF token, or
  with a foreign `Origin`, answers 403 and reaches no API route.
- The relay passes `Host` and `Origin` unchanged and forwards no
  `jatszoter_session`, `jatszoter_csrf` or signature header.
- A path under `/api/v1` outside the relay paths answers 404 from `web`.
- `GET /s/{token}` through `web` sets the link cookie and redirects to
  `/t/{deployment_id}`, and the access log shows `/s/<redacted>`.
- A trainee page types into the terminal, requests a check and sees its
  result; an operator page takes control and the trainee's keys stop.
- A revoked link closes the trainee's socket with 4403 through `web`.
- Every answer carries the headers of [Security headers](#security-headers),
  and no page references another host.
- The replay page plays a recording and seeks to each marker.
