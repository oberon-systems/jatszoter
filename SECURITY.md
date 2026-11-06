# Security policy

jatszoter runs exam VMs for trainees and keeps the scenarios and the
recordings of every exam. A flaw that lets a trainee leave their own
deployment, read a scenario's answer or change a recording is a security
issue.

## Supported versions

Fixes go into the next release of each package. Older releases get none.

| Package             | Supported            |
| ------------------- | -------------------- |
| `api-X.Y.Z`         | the latest release   |
| `web-X.Y.Z`         | the latest release   |
| `agent-X.Y.Z`       | the latest release   |
| meta release X.Y.Z  | the latest release   |

## Report a vulnerability

Do not open a public issue or pull request. Report it privately on GitHub:
the Security tab, then
[Report a vulnerability](../../security/advisories/new). The report stays
visible to you and the maintainers only, and the fix is discussed there.

Include:

- the package and its version, or the commit;
- what an attacker needs: a link, an operator login, root in a guest, access
  to the agent host;
- the steps to reproduce it and what they reach.

## Scope

The model and its limits are in
[docs/project/06-security.md](docs/project/06-security.md). Out of scope:

- a QEMU escape from the guest;
- what a trainee with root reads from their own VM's disk;
- a stand that ignores the stated requirements, for example a Valkey or a
  SeaweedFS filer reachable from guest networks.
