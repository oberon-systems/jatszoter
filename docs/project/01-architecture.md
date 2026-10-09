# 01 - Architecture

jatszoter is three services and a database. The API owns all persistent
state; agents turn it into running VMs; the web UI is a thin client of the
API.

- [Components](#components)
- [Stack](#stack)
- [Operator API](#operator-api)
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
 +---------+   HTTP + WS    +---------+   SQL    +----------+
 |   web   | -------------> |   api   | <------> | database |
 +---------+                +---------+          +----------+
                              ^     |  recordings, stream  +-----------+
            HTTP + WS (agent) |     +--------------------> | SeaweedFS |
                              |                            +-----------+
                         +---------+
                         |  agent  |  one per host
                         +---------+
                           |  QMP, serial socket, QGA socket
                           v
                         QEMU VM x N  (cloud-init, qemu-guest-agent)
```

- `api` - control plane: agents, leases, images, scenarios, deployments,
  links, recordings, checks. It is the only writer to the database and to
  the file store, and the only component browsers and agents talk to.
- `agent` - host daemon: registers, keeps its lease, starts and destroys
  VMs, bootstraps them, runs checks inside them, ships their consoles.
- `web` - operator and trainee pages. It holds no state of its own and
  relays the terminal WebSocket to the API.

## Stack

- API and web: Python 3.12+, [FastAPI](https://fastapi.tiangolo.com/),
  [Pydantic](https://docs.pydantic.dev/), [SQLModel](https://sqlmodel.tiangolo.com/),
  [python-statemachine](https://github.com/fgmacedo/python-statemachine),
  [httpx](https://www.python-httpx.org/).
- Database: any SQL database SQLModel can reach through its URL; the rules are
  in [02](02-domain-model.md#storage-rules). Migrations:
  [Alembic](https://alembic.sqlalchemy.org/).
- Files: the [SeaweedFS](https://github.com/seaweedfs/seaweedfs) filer at
  `JATSZOTER_SEAWEED_URL`. Without it the API writes the same layout under
  `data/storage/` in its working directory, the way fakeredis stands in for
  Valkey; where that directory lives is the environment's concern.
- Ephemeral state: [Valkey](https://valkey.io/), or an in-memory
  [fakeredis](https://github.com/cunla/fakeredis-py) when none is configured;
  see [02](02-domain-model.md#ephemeral-state).
- Web UI: Jinja2, [HTMX](https://htmx.org/), [xterm.js](https://xtermjs.org/),
  [asciinema-player](https://github.com/asciinema/asciinema-player); no
  JavaScript build step, vendored static files.
- Agent: Rust, [QEMU](https://www.qemu.org/) with KVM, cloud-init NoCloud,
  [qemu-guest-agent](https://wiki.qemu.org/Features/GuestAgent).
- Settings: environment variables with the `JATSZOTER_` prefix, read once
  when the app starts and passed on explicitly; models never read them.

## Operator API

Operators and the web UI call the API under `/api/v1`.

```text
POST /api/v1/auth/login                    open
POST /api/v1/auth/credential               signed (user)
POST /api/v1/auth/logout                   signed (user)
POST /api/v1/images                        signed (operator)
GET  /api/v1/images                        signed (operator)
POST /api/v1/scenarios                     signed (operator)
GET  /api/v1/scenarios                     signed (operator)
POST /api/v1/users                         signed (operator)
GET  /api/v1/users                         signed (operator)
POST /api/v1/deployments                   signed (operator)
GET  /api/v1/deployments                   signed (operator)
GET  /api/v1/deployments/{id}              signed (operator)
POST /api/v1/deployments/{id}/stop         signed (operator)
GET  /api/v1/deployments/{id}/console      signed (operator)
POST /api/v1/deployments/{id}/links        signed (operator)
GET  /api/v1/deployments/{id}/links        signed (operator)
POST /api/v1/deployments/{id}/control      signed (operator), operator link
WS   /api/v1/deployments/{id}/attach       signed (operator), link
POST /api/v1/links/{id}/revoke             signed (operator)
GET  /s/{token}                            open, the token is the secret
```

- Login and the credential are described in
  [06](06-security.md#credentials).
- A user request carries the owner id in `X-Owner`, the rest of the
  credential and the signature exactly like an agent request. An agent's
  owner id is the `agent_id` in the path instead.
- `signed (operator)` needs a user with role `operator`; any other signed
  user gets 403.
- A WebSocket route checks the signature from the upgrade's headers over
  `GET`, the path and an empty body. The console routes are described in
  [04](04-console.md).
- `link` is the `jatszoter_link` cookie that `GET /s/{token}` sets: a
  session of one link, valid for that link's deployment alone while the
  link is not revoked and its user is `enabled`. `operator link` is such a
  session whose user has role `operator`. See [04](04-console.md#links).
- `GET /s/{token}` is the one route outside `/api/v1`.

## Agent protocol

Agents call the API under `/api/v1/agents`; the API never connects to an
agent.

```text
POST /api/v1/agents/register                              open
POST /api/v1/agents/claim                                 claim code
POST /api/v1/agents/{agent_id}/heartbeat                  signed
POST /api/v1/agents/{agent_id}/credential                 signed
GET  /api/v1/agents/{agent_id}/deployments                signed
POST /api/v1/agents/{agent_id}/deployments/{id}/transition signed
GET  /api/v1/agents/{agent_id}/deployments/{id}/seed       signed
POST /api/v1/agents/{agent_id}/deployments/{id}/checks/{check_id} signed
WS   /api/v1/agents/{agent_id}/deployments/{id}/console   signed
```

- Registration is open and returns a one-time claim code. It lives only in
  Valkey with a TTL, so a registration no operator approves in time expires
  on its own and never reaches SQL. Once an operator approves it in the web
  UI, the agent is stored `enabled`, and the agent claims its id, credential
  and signing key with that code, in a response signed by the API; until
  then a claim gets 409.
- With `JATSZOTER_AUTO_REGISTER_AGENTS=true`, off by default and meant for
  the smoke stand, a registration is stored `enabled` at once and never waits
  in Valkey.
- An agent the API refuses, disabled or with a dead credential, registers
  again under its own id; the approval updates that agent instead of adding
  one.
- The agent keeps its id, claim code and credential in
  `$XDG_CACHE_HOME/jatszoter`, or `~/.cache/jatszoter`, readable by its user
  alone; `JATSZOTER_AGENT_STATE_DIR` moves it.
- Every agent request, its reports included, is signed with that key, see
  [06](06-security.md#credentials). The lease is not a credential: it only
  says the agent is alive and which deployments it holds.
- `POST .../credential` refreshes the credential before it expires, see
  [06](06-security.md#credentials).
- A heartbeat renews the agent's single lease and reports its capacity N,
  the number of VMs it may run at once. The answer carries the lease id, the
  heartbeat time and the deadline; the agent heartbeats every third of the
  lease TTL.
- A heartbeat also assigns PENDING deployments to the agent: first those the
  operator pinned to it, then unpinned ones, oldest first, up to its
  capacity minus the non-terminal deployments it already holds. The
  assignment is compare-and-swap on `agent_id`, so a deployment never lands
  on two agents, and it sets `vm_id`.
- A signed GET request signs an empty body.
- `GET .../deployments` is the desired state: every non-terminal deployment
  of the agent, and any pending check. A request without a live lease gets
  409. One entry is:

  ```text
  id, vm_id, status
  scenario   id, slug, version
  image      name, url, digest
  resources  cpus, memory_mib, disk_gib
  timeouts   bootstrap_seconds, check_seconds, session_minutes
  ```

- `GET .../seed` is the deployment's cloud-init seed:
  `{"user_data": "...", "meta_data": "..."}`, the two NoCloud files as text.
  The API renders them from the stored bundle of the deployment's scenario
  version, see [03](03-scenario-format.md#bootstrap).
- The seed is served only for a deployment the agent holds, only while that
  deployment is STARTING, and only under a live lease; anything else gets
  409. An unknown deployment, or another agent's, gets 404.
- A transition body is `event`, `expected` - the status the agent believes
  the deployment is in - and an optional `reason`. The agent events are
  `assign`, `boot`, `bootstrap`, `destroy` and `fail`.
- Transitions are compare-and-swap on `expected` and `agent_id`, accepted
  only while the agent's lease is live; anything else gets 409 and a
  repeated transition is a no-op.
- When a lease expires, the API fails the agent's STARTING, BOOTSTRAPPING,
  READY and STOPPING deployments before it grants that agent a new lease. The
  agent fences itself: once its local lease deadline passes, it destroys its
  VMs.

## VM runtime

- Everything lives under the agent's state directory:

  ```text
  images/<digest>.qcow2        cached base image, checked by digest
  vms/<deployment_id>/
    disk.qcow2                 overlay over the base image
    vm.json                    deployment_id, vm_id, created_at
    seed.iso                   cloud-init NoCloud seed, removed once ejected
    efivars.fd                 the VM's own UEFI variable store
    qemu.pid                   pid file of the daemonized QEMU
    qmp.sock                   QMP
    serial.sock                serial console
    console.log                everything the guest printed on ttyS0
    qga.sock                   qemu-guest-agent
  ```

- A base image is downloaded once per digest and shared by every VM that
  uses it. It streams into a 0600 part file and is hashed on the way; a
  wrong digest, a body over `JATSZOTER_AGENT_IMAGE_LIMIT_GIB` (default 20)
  or any other failure removes the part file. The URL and every redirect
  must use https unless they point to loopback.

- QEMU is daemonized with a pid file, so a VM outlives an agent restart and
  reconciliation adopts it. It runs with KVM when `/dev/kvm` is usable and
  with TCG otherwise.
- A VM boots through UEFI: QEMU maps the
  [OVMF](https://github.com/tianocore/tianocore.github.io/wiki/OVMF) code
  from `JATSZOTER_AGENT_OVMF_CODE` read-only and a 0600 copy of
  `JATSZOTER_AGENT_OVMF_VARS` as `efivars.fd`. The defaults are
  `/usr/share/OVMF/OVMF_CODE_4M.fd` and `/usr/share/OVMF/OVMF_VARS_4M.fd`,
  so OVMF must be installed on every agent host. Rocky 9 GenericCloud
  images reset in a loop under the default SeaBIOS firmware.
- A VM is created over its cached base image as a qcow2 overlay, with QEMU
  and `qemu-img` from `JATSZOTER_AGENT_QEMU` and `JATSZOTER_AGENT_QEMU_IMG`.
  A VM that fails to start leaves no directory and no process. A VM is alive
  while the pid in `qemu.pid` runs a command line carrying its vm id, so a
  reused pid is never taken for it. Destroy asks QEMU to quit over QMP, kills
  it if it does not, and removes the directory.
- `created_at` in `vm.json` is the VM's creation time in unix seconds.
- The agent packs the seed into `seed.iso` with `xorriso -as mkisofs`,
  volume label `cidata`, from `JATSZOTER_AGENT_XORRISO` (default `xorriso`),
  spawned with an argument vector like `qemu-img`. The file is 0600 and
  holds the seed of [03](03-scenario-format.md#bootstrap).
- QEMU attaches `seed.iso` read-only as a CD-ROM. After the marker says
  `ok`, the agent ejects it through QMP and deletes the file.
- Network is QEMU user-mode NAT: the guest reaches the internet for package
  installs, nothing reaches the guest from outside.
- The trainee's shell is the guest's serial console `ttyS0`; no SSH and no
  port forwarding are involved.
- QEMU logs the serial console to `console.log` from boot; the agent ships
  that file to the API, see [04](04-console.md#pipeline).
- Work inside the guest - bootstrap status, checks, terminal resize - goes
  through qemu-guest-agent `guest-exec` and `guest-file-*`, never through
  the console.
- The agent never runs scenario text or trainee input through a host shell.

## Data flows

Create and bootstrap:

1. Operator `POST /api/v1/deployments` with `scenario_id`, `trainee_id`
   and an optional `agent_id` to pin it; the API copies the scenario's
   image, resources and timeouts, stores it PENDING and answers with it and
   its trainee link.
2. A heartbeat assigns it; the agent moves it to STARTING, fetches the
   image and the seed, packs the seed ISO and boots the VM.
3. Once the guest agent answers `guest-ping`, the agent moves the
   deployment to BOOTSTRAPPING.
4. When the marker reads `ok`, the agent ejects the seed ISO and moves the
   deployment to READY. A marker `failed <step> <exit code>` moves it to
   FAILED with the marker as reason; so does `bootstrap_seconds` passing,
   with reason `bootstrap timeout`.

Console: see [04](04-console.md). Recording: see [05](05-recording.md).

Check:

1. Operator or trainee `POST /api/v1/deployments/{id}/checks`; the API
   stores a Check `pending`.
2. The agent sees it in its desired state, copies the check script into the
   guest, runs it with the scenario's `check_seconds` timeout, deletes it.
3. The agent posts exit code and the first stdout line; the API stores the
   result and shows it.

Stop:

1. Operator stops the deployment; STARTING, BOOTSTRAPPING and READY move
   to STOPPING, PENDING to CANCELLED.
2. The agent destroys the VM and its directory, then moves it to COMPLETED.
3. COMPLETED revokes the deployment's links; the API closes the recording
   stream and stores its final size and duration.

## Reconciliation

After a restart the agent lists its VM directories and QEMU processes and
compares them with the desired state. A VM the API still wants and that is
alive is adopted; one it no longer wants, or one it never knew, is destroyed.
A deployment the API wants but whose VM is gone is moved to FAILED with
reason `vm lost`, never silently rebooted.

- The same comparison runs after every accepted heartbeat. Each start,
  stop, lost VM or stale directory runs as its own task, so downloads and
  boots never delay a heartbeat, and a deployment is never worked on twice.
- A start moves the deployment to STARTING first and stops quietly when that
  transition conflicts. A failed start moves it to FAILED with a short reason
  and leaves no VM directory behind.
- Starts count against the capacity together with the running VMs and the
  starts in flight.
- A live VM whose deployment is STARTING or BOOTSTRAPPING is probed once per
  round: a short task pings the guest agent or reads the marker and checks
  the deadline, then ends. Nothing waits for a guest in a long task, so a
  stop is never held up by a bootstrap.
- The bootstrap deadline counts from `created_at` in `vm.json`, so an agent
  restart neither resets nor loses it.
- A deployment the API moved to STOPPING while a start task for it is still
  running has that task aborted; the stop then destroys whatever it left.
- Tasks sign with the agent's live credential, and each request holds it for
  at most 15 seconds, so a refresh never waits on a long task. Fencing aborts
  every task, then destroys every VM on the host.

## Acceptance criteria

- Every arrow in the component diagram has an owner and an authentication
  method named in this document or in [06](06-security.md).
- No flow requires the API to open a connection to an agent.
- The lifecycle used here matches [02](02-domain-model.md).
