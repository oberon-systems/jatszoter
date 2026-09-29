# 06 - Security

jatszoter protects access to deployments, not the host from the guest. The
primary invariant, from `AGENTS.md`: a trainee reaches only the deployment
their link was issued for, and only while that link is valid and the
operator has not taken control.

- [Model](#model)
- [Credentials](#credentials)
- [Share link threats](#share-link-threats)
- [Guest and host](#guest-and-host)
- [Scenario secrecy](#scenario-secrecy)
- [Web](#web)
- [Acceptance criteria](#acceptance-criteria)

## Model

| Asset                     | Protected against            | How                              |
| ------------------------- | ---------------------------- | -------------------------------- |
| Deployments and their VMs | anyone without a credential  | operator token, share link       |
| Other trainees' sessions  | a trainee with a link        | link scoped to one deployment    |
| Operator control          | a trainee with a modified UI | enforced in the API              |
| Agent identity            | a stolen enrollment token    | rotating, hashed agent tokens    |
| The host                  | a hostile guest              | not protected beyond QEMU itself |

## Credentials

- **Operator token** - bearer token; the API holds only its sha256 in
  `JATSZOTER_OPERATOR_TOKEN_SHA256`. OIDC is on the [roadmap](07-roadmap.md).
- **Enrollment token** - opens registration only; stored as sha256, and
  registration is closed while it is unset.
- **Agent token** - random, returned once, stored as sha256, rotated by
  heartbeat past half its lifetime. A token used on another agent's path
  gets 403; a revoked agent gets 401.
- **Share link** - see [04](04-console.md#share-links).
- No token, link or key ever reaches a log line, an audit record or a
  recording.

## Share link threats

| Threat                         | Mitigation                                        |
| ------------------------------ | ------------------------------------------------- |
| Link forwarded to someone else | TTL, one live link per deployment, revoke         |
| Link leaks through `Referer`   | token swapped for a cookie, `no-referrer` policy  |
| Link left in browser history   | redirect to a URL without the token               |
| Link guessed                   | 256 random bits, constant-time hash comparison    |
| Link used after the exam       | expires with the session and the deployment       |
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
- Operator pages and trainee pages are separate routes; a share-link cookie
  opens nothing outside its deployment's trainee page, attach socket and
  check.

## Acceptance criteria

- Each row of the share link threat table has a test.
- A trainee cookie gets 403 on every operator route and on another
  deployment.
- A grep of logs and recordings after a full exam finds no token or link.
