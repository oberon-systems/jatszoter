# 06 - Security

jatszoter protects access to deployments, not the host from the guest. The
primary invariant, from `AGENTS.md`: a trainee reaches only the deployment
their link was issued for, and only while that link is valid and the
operator has not taken control.

- [Model](#model)
- [Credentials](#credentials)
- [Link threats](#link-threats)
- [Guest and host](#guest-and-host)
- [Image downloads](#image-downloads)
- [Bundle downloads](#bundle-downloads)
- [Request bodies](#request-bodies)
- [Images](#images)
- [Valkey](#valkey)
- [Scenario secrecy](#scenario-secrecy)
- [Web](#web)
- [Error answers](#error-answers)
- [Acceptance criteria](#acceptance-criteria)

## Model

| Asset                     | Protected against            | How                              |
| ------------------------- | ---------------------------- | -------------------------------- |
| Deployments and their VMs | anyone without a credential  | operator login, link             |
| Other trainees' terminals | a trainee with a link        | link scoped to one deployment    |
| Operator control          | a trainee with a modified UI | enforced in the API              |
| Agent identity            | an open registration         | operator enables, signed calls   |
| Operator login            | password guessing            | scrypt, same 401, rate limit     |
| The API's outbound fetch  | a scenario URL               | operators only, address rule     |
| The agent's image fetch   | an image server              | address rule on each connection  |
| The host                  | a hostile guest              | not protected beyond QEMU itself |

## Credentials

- **Operator** - logs in with name and password on the login page of
  `web`, which calls `POST /api/v1/auth/login`, gets a credential and
  signing key, like an agent's claim, and keeps them in an encrypted
  cookie, see [08](08-web-ui.md#operator-session). On first start, when no operator exists, the API creates `admin` /
  `admin` with `password_changed` 0; such a login is refused until it
  sends `new_password`. OIDC is on the [roadmap](07-roadmap.md).
- **Passwords** - at least 12 characters and three of four classes:
  lowercase, uppercase, digits, other characters, see
  [08](08-web-ui.md#password-rule). Stored as scrypt hashes, the parameters
  kept with the hash. An unknown user and a wrong password get the same 401.
- **Login throttling** - failures are counted in Valkey within a 15-minute
  window, and a login over a limit gets 429, the right password included:
  - a browser with a valid device token: 10 failures for that token, and
    nothing else is counted or checked;
  - any other client: 10 failures for its address, an IPv6 one by its
    /64, and 100 for the name, so failures from many addresses lock the
    name for every browser without a device token.

  A success clears the counters it was checked against.
- **Device token** - every successful login answers one,
  `<kid>.<expires_at>.<nonce>.<mac>`, valid 90 days: the HMAC-SHA256 of
  `device:<user id>:<expires_at>:<nonce>` under the master key named by
  `kid`. The API stores none and derives the HMAC again on each login, so
  a token of another user, an altered or an expired one counts as none.
  It grants nothing beyond its own counter, so neither logout nor a new
  password ends it; retiring the master key does. `web` keeps it in the
  `jatszoter_device` cookie, see [08](08-web-ui.md#operator-session).
- **Client address** - `web` logs in on the browser's behalf and sends its
  address in `X-Forwarded-For`. The API takes that header only from a peer
  in uvicorn's `FORWARDED_ALLOW_IPS`, `127.0.0.1` by default; a deployment
  where `web` reaches the API from another address sets it to `web`'s, see
  [10](10-reverse-proxy.md#trust-the-proxy).
  Otherwise every browser counts as `web`'s address and the address limit
  acts as a second limit per name.
- **Open registration** - `POST /api/v1/agents/register` is anonymous, so
  it is rate limited per client address and in total and caps the
  registrations waiting for an operator, see
  [01](01-architecture.md#agent-protocol). A flood can keep real agents out for
  one window; they retry on their own.
- **Auto registration** - `JATSZOTER_AUTO_REGISTER_AGENTS=true` stores every
  registering agent `enabled` without an operator. It is off by default and
  belongs on a smoke stand, never on an API others can reach.
- **Credential** - agents and users sign every request. A request carries
  the credential in the open - owner id (the path for an agent, `X-Owner`
  for a user), `kid`, `generation`, `expires_at` - plus `X-Timestamp` and
  `X-Signature`:
  the HMAC-SHA256 of the timestamp, method, path and sha256 of the body, one
  per line. A request with a query signs `path?query`, the query exactly as
  sent, so no parameter can be changed under a valid signature. The signing
  key is never stored: it is the HMAC of `owner id : generation :
  expires_at` under the master key named by `kid`, so the API
  derives it again on every request, and an altered credential yields a
  different key and a signature that does not match. The master keys live in
  `JATSZOTER_MASTER_KEYS`, the one issuing new credentials in
  `JATSZOTER_MASTER_KID`; keeping the previous key listed lets older
  credentials run out smoothly. The API answers yes or no: the owner is
  `enabled`, the credential has not expired, the timestamp is within 300
  seconds, the signature matches. Anything else gets 401.
- **Refresh** - the `secrets` table keeps one row per owner: the live
  `generation` and a `pending_generation`. A credential lives an hour and is
  due for refresh 15 minutes before it expires. Every refresh hands out a
  fresh pending generation;
  the live one keeps working until the pending one signs its first request.
  An agent's superseded generation that still signs means two parties
  hold the key: the agent turns `disabled` at once, and only an operator
  enables it again. A stolen key therefore lasts at most until the next
  refresh. A user's superseded generation gets 401 and disables nobody: a
  user's key never leaves `web`, and its tabs race the refresh.
- **Logout** - `POST /api/v1/auth/logout` ends every credential the user
  holds: the live generation moves past both the live and the pending one.
  A credential issued before is then a superseded generation and gets
  401.
- **Link** - the token is exchanged once for a session cookie, see
  [04](04-console.md#links). A request or an upgrade that authenticates by
  that cookie is bound by SameSite=Strict and by its `Origin`, which, when
  sent, must name the request's own host.
- **Scenario URLs** - the API fetches them, so only an operator can name
  one, and the fetch rules in
  [03](03-scenario-format.md#registering) bound it.
- No token, link or key ever reaches a log line, an audit record or a
  recording.

## Link threats

| Threat                         | Mitigation                                        |
| ------------------------------ | ------------------------------------------------- |
| Link forwarded to someone else | revoke, and a new link starts a new recording     |
| Link leaks through `Referer`   | token swapped for a cookie, `same-origin` policy  |
| Link left in browser history   | redirect to a URL without the token               |
| Link guessed                   | 256 random bits, constant-time hash comparison    |
| Link used after the exam       | revoked when the deployment ends                  |
| Cookie reused on another host  | HttpOnly, Secure, SameSite=Strict, scoped path    |
| Cookie sent to another route   | `Path=/api/v1/deployments/{id}` of its deployment |
| Token read from the access log | the log shows `/s/<redacted>`                     |

## Guest and host

- The guest is not a security boundary. A trainee has root in the VM, and a
  QEMU escape is out of scope.
- QEMU runs as the agent's unprivileged user with KVM. VM directories are
  0700, which keeps everyone but that user away from the sockets, logs and
  disks inside them, whatever modes those files have.
- passt gives the guest outbound NAT and nothing inbound. It runs with
  `--no-map-gw`, so the gateway does not lead to the agent host's loopback:
  services bound to `127.0.0.1` are out of the guest's reach. QEMU's own
  user-mode network is not used because its gateway does.
- passt older than `2026_10_02` forwards a guest packet sent to
  `127.0.0.1` to the host's loopback. `jatszoter-agent check-network`
  refuses such a host; see [09](09-guest-network.md).
- Services the agent host binds to its other addresses stay reachable from
  the guest, as from any client on the network; firewall them accordingly.
- The agent never passes scenario text or trainee input to a host shell;
  every host process is spawned with an argument vector.
- The seed enables the guest agent's `guest-file-*` and `guest-exec*`
  calls, which RHEL-family guests block by default. That is no loss, since
  the guest is not a security boundary.

## Image downloads

The image server is not trusted, so it must not be able to steer the agent
to the agent host's loopback or to internal hosts. The API checks only the
scheme of an image URL, because the agent has its own network and DNS. The
agent checks every address it connects to.

- Allowed addresses: public ones, and those in
  `JATSZOTER_AGENT_IMAGE_INTERNAL_CIDRS`, a comma-separated list that is
  empty by default. Set it for an internal mirror, for example
  `192.0.2.0/24`.
- Refused addresses: loopback, private, link-local, CGNAT, multicast,
  documentation and unspecified ones. An IPv4-mapped IPv6 address counts as
  the IPv4 address it maps.
- Names: if any address in a DNS answer is refused, the whole answer is
  refused. The agent connects only to addresses it checked, so DNS
  rebinding does not get past the rule.
- Redirects: followed under the same rules, up to ten hops. The agent sends
  no `Referer`, so a token in the image URL does not reach the next host.
- Deadline: the whole download, redirects included, must finish within
  `JATSZOTER_AGENT_IMAGE_DOWNLOAD_MINUTES`, 60 by default and at most 1440.
  A slow server cannot hold the per-digest lock past it. At the default, a
  20 GiB image needs about 48 Mbit/s, so raise it for a slower link.
- Failures: the operator sees a fixed reason without the address. The
  address is in the agent's log.

The agent ignores proxy variables for image downloads by default, because
through a proxy it can check only the proxy's name.
`JATSZOTER_AGENT_IMAGE_USE_PROXY=true` makes it honor them and log a warning
at start. Names of image hosts are then not checked, and the proxy decides
what they reach. IP literals in URLs and redirects are still checked. A
proxy whose name resolves to an internal address needs its network in
`JATSZOTER_AGENT_IMAGE_INTERNAL_CIDRS`.

An image server can still send the agent's GET to a public host. The digest
protects the image in that case.

## Bundle downloads

A bundle URL must not make the API reach its own loopback or internal hosts.
The API checks every address a bundle URL resolves to, with the ranges of
[Image downloads](#image-downloads), before it connects.

- Allowed addresses: public ones, and those in
  `JATSZOTER_SCENARIO_FETCH_INTERNAL_CIDRS`, a comma-separated list that is
  empty by default. Set it for an internal server, for example
  `192.0.2.0/24`.
- Names: if any address in a DNS answer is refused, the whole answer is
  refused. The API connects only to addresses it checked and verifies the
  certificate against the name, so DNS rebinding does not get past the rule.
- Proxies: the API ignores proxy variables for bundle downloads, because a
  proxy would take the request past the check.
- Failures: a refused address answers `bundle-fetch-refused`. A connection
  failure and a status other than 200 both answer `bundle-fetch-failed`, so
  the answer does not map a network. The API log has the host and the cause
  under the answer's error id, and never the rest of the URL.

## Request bodies

Anyone who reaches `api` or `web` can send a body, so both refuse one past
its limit with 413 before a signature or a CSRF token is checked. They check
`Content-Length` first and then count the bytes as they arrive, so a chunked
body is cut at the same limit.

- Default: 64 KiB.
- `POST /api/v1/scenarios` in `api`:
  `JATSZOTER_SCENARIO_BUNDLE_LIMIT_BYTES` plus 64 KiB for the multipart
  framing.
- `POST /scenarios` in `web`: 10 MiB plus the same 64 KiB.
- WebSockets are not covered. The console and the recordings have their own
  limits.

No stand needs a reverse proxy. A deployment that adds one should set the
same limits there, see [10](10-reverse-proxy.md#configure-nginx).

## Images

The API and web images install what was reviewed, not what PyPI serves at
build time.

- Dependencies: `packages/{api,web}/requirements.txt` holds every version
  with its hashes, and the build refuses anything else. `make lock` rewrites
  it from `requirements.in`, and `LOCK_ARGS=--upgrade` moves the versions.
- Wheels only, with one exception: PyYAML has no wheel for Python 3.15 yet,
  so it builds from its hash-checked source with the locked setuptools.
- Build backend: `requirements-build.txt` pins setuptools for the image and
  for the published wheels.
- Base image: named by digest.
- Updates: Dependabot proposes them after a 14-day cooldown.
- Drift: `pip check` fails the build when `pyproject.toml` names a dependency
  the lock lacks.

## Valkey

Write access to Valkey must not become code execution in the API, or a claim
code or link session of the writer's choosing.

- Entries read through `_get`: registrations, claim codes, link sessions,
  leases and cached secrets. They are JSON, signed with HMAC-SHA256 under a
  key derived from the master key. Valkey never sees that key.
- A signature covers the entry's key, so a value cannot be moved to another
  entry. An entry that fails the check counts as a miss and is logged.
- Master key rotation: an entry stays readable while its kid is listed in
  `JATSZOTER_MASTER_KEYS`.
- Not signed: login counters, the console tail and its offsets, control of a
  console, and recording pointers. A writer cannot run code through them, but
  can change them. A writer can also delete any entry, or put back an older
  signed value of the same entry.

Keep Valkey private, with a password.

## Scenario secrecy

Hiding the fault from the trainee is a courtesy, not a guarantee: a trainee
with root can inspect the disk. The bootstrap removes the obvious traces -
step scripts, their log, cloud-init's `user-data` - and ejects the seed ISO;
the check script is present only while it runs. Exams that need more should
use scenarios whose fault is not readable from the disk alone.

The seed carries the break step, so it is guarded on the way in:

- The seed route answers only the agent holding the deployment, and only
  while the deployment is STARTING.
- The agent never logs a seed, keeps `seed.iso` 0600 inside the VM
  directory, and deletes it after the eject.

The check script is guarded the same way:

- Its route answers only the agent holding the deployment, under a live
  lease, while the deployment is READY and that check is pending.
- Neither side logs the script or what it printed. The stderr kept as a
  check's detail reaches an operator only, never a trainee or viewer
  link.

## Web

- A cookie request that changes state and a cookie WebSocket upgrade are
  refused when they send an `Origin` that does not name their own host;
  SameSite=Strict keeps the cookie off cross-site requests. `web` checks a
  CSRF token on every state-changing request, an operator's and a link's,
  before it reaches the API; see [08](08-web-ui.md#csrf).
- Signed requests carry no cookie and need neither.
- Security headers: `Content-Security-Policy` without inline scripts,
  `Referrer-Policy: same-origin`, `X-Frame-Options: DENY`; the full set is
  in [08](08-web-ui.md#security-headers).
- Operator pages and trainee pages are separate routes; a link cookie
  reaches nothing outside its deployment's routes under
  `/api/v1/deployments/{id}`: the attach socket, control, the checks and
  the task.

## Error answers

An error answer tells the caller what went wrong in a stable code, never in
the words of a driver, a library or an exception. Those words name tables,
columns, hosts and the caller's own input, so they stay in the server log.

- Body: every error answer of `api`, and of `web` under `/api/`, is
  `{"error": "<code>", "error_id": "<uuid4>"}` and nothing else. A 422
  names the first failing top-level field, as `deployment-id-missing` or
  `name-invalid`, and `body-invalid` for a body that does not parse.
- Log: the same line holds the error id, the code, the status, the method,
  the path with link tokens redacted, and the real message; a 500 also
  holds the traceback. An operator finds the cause from the id a client
  shows.
- Database: a unique constraint answers 409 with the code of the model, such
  as `user-name-taken` or `agent-already-registered`; the driver's message
  goes to the log only.
- Relay: `web` passes the code and the id of an API refusal on to a trainee
  and drops the rest. A body the API did not shape, such as a bare 500, is
  replaced with `web`'s own code and id, logged by `web`.
- Text: `web` turns a code into a sentence of its own table
  (`jatszoter_web/texts.py`) for a signed-in operator only. The login page
  and the trainee page show `The request was refused` and the error id.

## Acceptance criteria

- Each row of the link threat table has a test.
- A trainee cookie gets 403 on every operator route and on another
  deployment.
- A grep of logs and recordings after a full exam finds no token or link.
- No error answer of `api` or `web` carries a `detail`, a driver or parser
  message, or the input it was sent.
