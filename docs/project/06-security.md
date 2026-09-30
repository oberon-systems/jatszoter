# 06 - Security

jatszoter protects access to deployments, not the host from the guest. The
primary invariant, from `AGENTS.md`: a trainee reaches only the deployment
their link was issued for, and only while that link is valid and the
operator has not taken control.

- [Model](#model)
- [Credentials](#credentials)
- [Link threats](#link-threats)
- [Guest and host](#guest-and-host)
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
| The host                  | a hostile guest              | not protected beyond QEMU itself |

## Credentials

- **Operator** - logs in as a user; the first login as `admin` / `admin`
  must change the password. OIDC is on the [roadmap](07-roadmap.md).
- **Auto registration** - `JATSZOTER_AUTO_REGISTER_AGENTS=true` stores every
  registering agent `enabled` without an operator. It is off by default and
  belongs on a smoke stand, never on an API others can reach.
- **Credential** - agents now, and users once authentication exists, sign
  every request. A request carries the credential in the open - owner id,
  `kid`, `generation`, `expires_at` - plus `X-Timestamp` and `X-Signature`:
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
  A superseded generation that still signs means two parties hold the key:
  the owner turns `disabled` at once, and only an operator enables it again.
  A stolen key therefore lasts at most until the next refresh.
- **Link** - see [04](04-console.md#links).
- No token, link or key ever reaches a log line, an audit record or a
  recording.

## Link threats

| Threat                         | Mitigation                                        |
| ------------------------------ | ------------------------------------------------- |
| Link forwarded to someone else | revoke, and a new link starts a new recording     |
| Link leaks through `Referer`   | token swapped for a cookie, `no-referrer` policy  |
| Link left in browser history   | redirect to a URL without the token               |
| Link guessed                   | 256 random bits, constant-time hash comparison    |
| Link used after the exam       | revoked when the deployment ends                  |
| Cookie reused on another host  | HttpOnly, Secure, SameSite=Strict, scoped path    |

## Guest and host

- The guest is not a security boundary. A trainee has root in the VM, and a
  QEMU escape is out of scope.
- QEMU runs as the agent's unprivileged user with KVM; VM directories and
  sockets are 0700.
- User-mode NAT gives the guest outbound access and nothing inbound. The
  host itself stays reachable from the guest through the NAT gateway;
  firewall host services accordingly.
- The agent never passes scenario text or trainee input to a host shell;
  every host process is spawned with an argument vector.

## Scenario secrecy

Hiding the fault from the trainee is a courtesy, not a guarantee: a trainee
with root can inspect the disk. The bootstrap removes the obvious traces -
step scripts, their log, cloud-init's `user-data` - and ejects the seed ISO;
the check script is present only while it runs. Exams that need more should
use scenarios whose fault is not readable from the disk alone.

## Web

- Every state-changing request needs a CSRF token; WebSocket upgrades check
  `Origin`.
- Security headers: `Content-Security-Policy` without inline scripts,
  `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`.
- Operator pages and trainee pages are separate routes; a link cookie
  opens nothing outside its deployment's trainee page, attach socket and
  check.

## Acceptance criteria

- Each row of the link threat table has a test.
- A trainee cookie gets 403 on every operator route and on another
  deployment.
- A grep of logs and recordings after a full exam finds no token or link.
