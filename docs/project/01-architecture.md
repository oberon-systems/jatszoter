# 01 - Architecture

jatszoter is three services and a database. The API owns all persistent
state; agents turn it into running VMs; the web UI is a thin client of the
API.

- [Components](#components)
- [Stack](#stack)
- [Agent protocol](#agent-protocol)
- [VM runtime](#vm-runtime)
- [Data flows](#data-flows)
- [Reconciliation](#reconciliation)
- [Acceptance criteria](#acceptance-criteria)

## Components

```text
 browser (operator / trainee)
      |  HTTP + WebSocket
      v
 +---------+   HTTP + WS    +---------+   SQL    +------------+
 |   web   | -------------> |   api   | <------> | PostgreSQL |
 +---------+                +---------+          +------------+
                              ^     |
            HTTP + WS (agent) |     | recordings on disk
                              |     v
                         +---------+
                         |  agent  |  one per host
                         +---------+
                           |  QMP, serial socket, QGA socket
                           v
                         QEMU VM x N  (cloud-init, qemu-guest-agent)
```

- `api` - control plane: agents, leases, images, scenarios, deployments,
  share links, sessions, recordings, check results. It is the only writer to
  the database and the only component browsers and agents talk to.
- `agent` - host daemon: registers, keeps its lease, starts and destroys
  VMs, bootstraps them, runs checks inside them, ships their consoles.
- `web` - operator and trainee pages. It holds no state of its own and
  relays the terminal WebSocket to the API.

## Stack

- API and web: Python 3.12+, [FastAPI](https://fastapi.tiangolo.com/),
  [Pydantic](https://docs.pydantic.dev/), [SQLModel](https://sqlmodel.tiangolo.com/),
  [Alembic](https://alembic.sqlalchemy.org/), [httpx](https://www.python-httpx.org/).
- Database: [PostgreSQL](https://www.postgresql.org/).
- Web UI: Jinja2, [HTMX](https://htmx.org/), [xterm.js](https://xtermjs.org/),
  [asciinema-player](https://github.com/asciinema/asciinema-player); no
  JavaScript build step, vendored static files.
- Agent: Rust, [QEMU](https://www.qemu.org/) with KVM, cloud-init NoCloud,
  [qemu-guest-agent](https://wiki.qemu.org/Features/GuestAgent).
- Settings: environment variables with the `JATSZOTER_` prefix.

## Agent protocol

Agents call the API under `/api/v1/agents`; the API never connects to an
agent.

```text
POST /api/v1/agents/register                              enrollment token
POST /api/v1/agents/{agent_id}/heartbeat                  agent token
GET  /api/v1/agents/{agent_id}/deployments                agent token
POST /api/v1/agents/{agent_id}/deployments/{id}/transition agent token
POST /api/v1/agents/{agent_id}/deployments/{id}/checks/{check_id} agent token
WS   /api/v1/agents/{agent_id}/deployments/{id}/console   agent token
```

- Registration exchanges the enrollment token for a rotating agent token.
- A heartbeat renews the agent's single lease and reports its capacity N,
  the number of VMs it may run at once. The API assigns PENDING deployments
  up to that capacity, compare-and-swap, so a deployment never lands on two
  leases.
- `GET .../deployments` is the desired state: every non-terminal deployment
  on the live lease with its scenario version, image URL and digest, and any
  pending check.
- Transitions are compare-and-swap on status and lease; a stale lease gets
  409 and a repeated transition is a no-op.
- A lease that expires fails its STARTING, BOOTSTRAPPING, READY and STOPPING
  deployments; its PENDING ones return to the pool. The agent fences itself:
  once its local lease deadline passes, it destroys its VMs.

## VM runtime

- Each VM has its own directory: a qcow2 overlay over a cached base image
  checked by digest, the cloud-init seed ISO, and unix sockets for QMP, the
  serial console and qemu-guest-agent.
- Network is QEMU user-mode NAT: the guest reaches the internet for package
  installs, nothing reaches the guest from outside.
- The trainee's shell is the guest's serial console `ttyS0`; no SSH and no
  port forwarding are involved.
- Work inside the guest - bootstrap status, checks, terminal resize - goes
  through qemu-guest-agent `guest-exec` and `guest-file-*`, never through
  the console.
- The agent never runs scenario text or trainee input through a host shell.

## Data flows

Create and bootstrap:

1. Operator `POST /api/v1/deployments` with `scenario_id`, optional
   `agent_id`; the API stores it PENDING.
2. A heartbeat assigns it; the agent moves it to STARTING, fetches the
   image, builds the seed ISO ([03](03-scenario-format.md)), boots the VM.
3. Once the guest agent answers, the deployment moves to BOOTSTRAPPING.
4. When the bootstrap marker reports success, the agent ejects the seed ISO
   and moves the deployment to READY.

Console: see [04](04-console.md). Recording: see [05](05-recording.md).

Check:

1. Operator or trainee `POST /api/v1/deployments/{id}/checks`; the API
   stores a CheckResult `pending`.
2. The agent sees it in its desired state, copies the check script into the
   guest, runs it with the scenario's `check_seconds` timeout, deletes it.
3. The agent posts exit code and the first stdout line; the API stores the
   result and shows it.

Stop:

1. Operator stops the deployment; READY moves to STOPPING, PENDING to
   CANCELLED.
2. The agent destroys the VM and its directory, then moves it to COMPLETED.
3. The API closes the open session and finalizes its recording.

## Reconciliation

After a restart the agent lists its VM directories and QEMU processes and
compares them with the desired state. A VM the API still wants and that is
alive is adopted; one it no longer wants, or one it never knew, is destroyed.
A deployment the API wants but whose VM is gone is moved to FAILED with
reason `vm lost`, never silently rebooted.

## Acceptance criteria

- Every arrow in the component diagram has an owner and an authentication
  method named in this document or in [06](06-security.md).
- No flow requires the API to open a connection to an agent.
- The lifecycle used here matches [02](02-domain-model.md).
