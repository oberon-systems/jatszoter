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
- [Request bodies](#request-bodies)
- [Scenario secrecy](#scenario-secrecy)
- [Web](#web)
- [Acceptance criteria](#acceptance-criteria)

## Model

| Asset                     | Protected against            | How                              |
| ------------------------- | ---------------------------- | -------------------------------- |
| Deployments and their VMs | anyone without a credential  | operator login, link             |
| Other trainees' terminals | a trainee with a link        | link scoped to one deployment    |
| Operator control          | a trainee with a modified UI | enforced in the API              |
| Agent identity            | an open registration         | operator enables, signed calls   |
| Operator login            | password guessing            | scrypt, same 401, rate limit     |
| The API's outbound fetch  | a scenario URL               | operators only, fetch rules      |
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
  kept with the hash. An unknown user and a wrong password get the same 401. Ten failures
  for one name within 15 minutes get 429 until the window passes; the
  counter lives in Valkey.
- **Auto registration** - `JATSZOTER_AUTO_REGISTER_AGENTS=true` stores every
  registering agent `enabled` without an operator. It is off by default and
  belongs on a smoke stand, never on an API others can reach.
- **Credential** - agents and users sign every request. A request carries
  the credential in the open - owner id (the path for an agent, `X-Owner`
  for a user), `kid`, `generation`, `expires_at` - plus `X-Timestamp` and
  `X-Signature`:
  the HMAC-SHA256 of the timestamp, method, path and sha256 of the body, one
  per line. The signing key is never stored: it is the HMAC of `owner id :
  generation : expires_at` under the master key named by `kid`, so the API
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
- QEMU runs as the agent's unprivileged user with KVM; VM directories and
  sockets are 0700.
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
same limits there.

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

## Acceptance criteria

- Each row of the link threat table has a test.
- A trainee cookie gets 403 on every operator route and on another
  deployment.
- A grep of logs and recordings after a full exam finds no token or link.
