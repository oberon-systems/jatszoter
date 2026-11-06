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
  the file store, and the only component agents talk to.
- `agent` - host daemon: registers, keeps its lease, starts and destroys
  VMs, bootstraps them, runs checks inside them, ships their consoles.
- `web` - operator and trainee pages, and the one origin browsers talk to.
  It holds no state of its own, signs an operator's calls to the API and
  relays a link's requests and terminal WebSocket to it unchanged, see
  [08](08-web-ui.md).

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

Operators and the web UI call the API under `/api/v1`. What each screen of
the web UI calls is listed in [08](08-web-ui.md#screens).

```text
POST /api/v1/auth/login                    open
POST /api/v1/auth/credential               signed (user)
POST /api/v1/auth/logout                   signed (user)
GET  /api/v1/auth/me                       signed (user)
GET  /api/v1/agents                        signed (operator)
POST /api/v1/agents/{agent_id}/state       signed (operator)
POST /api/v1/images                        signed (operator)
GET  /api/v1/images                        signed (operator)
POST /api/v1/scenarios                     signed (operator)
GET  /api/v1/scenarios                     signed (operator)
POST /api/v1/users                         signed (operator)
GET  /api/v1/users                         signed (operator)
POST /api/v1/users/{id}/state              signed (operator)
POST /api/v1/users/{id}/password           signed (operator)
POST /api/v1/deployments                   signed (operator)
GET  /api/v1/deployments                   signed (operator)
GET  /api/v1/deployments/{id}              signed (operator)
POST /api/v1/deployments/{id}/stop         signed (operator)
POST /api/v1/deployments/{id}/purge        signed (operator)
GET  /api/v1/deployments/{id}/task         signed (operator), link
GET  /api/v1/deployments/{id}/console      signed (operator)
POST /api/v1/deployments/{id}/links        signed (operator)
GET  /api/v1/deployments/{id}/links        signed (operator)
GET  /api/v1/deployments/{id}/recordings   signed (operator)
POST /api/v1/deployments/{id}/control      signed (operator), operator link
POST /api/v1/deployments/{id}/checks       signed (operator), operator or trainee link
GET  /api/v1/deployments/{id}/checks       signed (operator), link
GET  /api/v1/deployments/{id}/checks/{id}  signed (operator), link
WS   /api/v1/deployments/{id}/attach       signed (operator), link
GET  /api/v1/deployments/{id}/disk-check   signed (operator)
GET  /api/v1/checks                        signed (operator)
GET  /api/v1/storage                       signed (operator)
POST /api/v1/links/{id}/revoke             signed (operator)
GET  /api/v1/recordings/{id}.cast          signed (operator)
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
- The recording routes are described in [05](05-recording.md#replay); no
  link reads a recording.
- The check routes answer `id`, `status`, `exit_code`, `message`,
  `created_at` and `finished_at`, and `detail` for an operator only: a
  trainee or viewer link gets no such field. A viewer link reads checks
  and requests none. The flow is under [Data flows](#data-flows).
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
GET  /api/v1/agents/{agent_id}/deployments/{id}/checks/{check_id} signed
POST /api/v1/agents/{agent_id}/deployments/{id}/checks/{check_id} signed
POST /api/v1/agents/{agent_id}/deployments/{id}/disk-check signed
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
- An agent id never rebinds: a registration naming a stored or waiting id is
  refused with 409, before any key is written.
- Registration is limited per client address and in total, 10 and 60
  within 10 minutes by default (`JATSZOTER_AGENT_REGISTER_PER_ADDRESS`,
  `JATSZOTER_AGENT_REGISTER_TOTAL`); a call over either gets 429 with
  `too many registrations`. The address is the TCP peer, so behind a reverse
  proxy every agent shares one.
- At most `JATSZOTER_AGENT_PENDING_LIMIT` registrations, 100 by default,
  wait for an operator; one more gets 429 with `too many registrations wait
  for an operator`, before any key is written. An agent that gets 429 keeps
  its id and tries again on its next cycle.
- An agent the API refuses, disabled or with a dead credential, registers
  again under its own id, gets that 409, forgets the id and registers as a
  new agent for an operator to approve. The old row stays, and a deployment
  pinned to it keeps waiting until an operator deals with it.
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
- A heartbeat also reports the agent's accelerator: `kvm`, `tcg`, or `none`
  when a VM start would be refused. The field is optional, so an older agent
  keeps working. An older API refuses it with 422, so upgrade the API before
  the agents.
- A heartbeat also assigns PENDING deployments to the agent: first those the
  operator pinned to it, then unpinned ones, oldest first, up to its
  capacity minus the non-terminal deployments it already holds. The
  assignment is compare-and-swap on `agent_id`, so a deployment never lands
  on two agents, and it sets `vm_id`.
- An agent that reports the accelerator `none` gets only the deployments
  pinned to it, and those fail at once. Unpinned ones wait for an agent that
  can run them. With no such agent they stay PENDING with no fail reason, and
  the Agents page shows why.
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
  limits     console_bytes
  check      the id of the pending check, or null
  outside    network: the scenario's probes
             disk: the files to read, only while a verdict is owed
  ```

- `GET .../seed` is the deployment's cloud-init seed:
  `{"user_data": "...", "meta_data": "..."}`, the two NoCloud files as text.
  The API renders them from the stored bundle of the deployment's scenario
  version, see [03](03-scenario-format.md#bootstrap).
- The seed is served only for a deployment the agent holds, only while that
  deployment is STARTING, and only under a live lease; anything else gets
  409. An unknown deployment, or another agent's, gets 404.
- `GET .../checks/{check_id}` is the script of a pending check:
  `{"script": "...", "timeout_seconds": n}`, the scenario's check script
  as text from the stored bundle and the deployment's `check_seconds`.
- `POST .../checks/{check_id}` is its result: `status` (`passed`,
  `failed`, `error` or `timeout`), `exit_code`, `message` and `detail`.
  `passed` goes with exit code 0, `failed` with any other, `timeout` and
  `error` with none; a body that says otherwise gets 422.
- Both check routes answer only the agent holding the deployment, under a
  live lease, while the deployment is READY and the check is `pending`;
  anything else gets 409. An unknown deployment or check, or another
  agent's deployment, gets 404.
- `POST .../disk-check` is the verdict on a stopped VM's disk: `status` and
  `message`. It answers only while the deployment is STOPPING and owes one;
  anything else gets 409.
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
    passt.pid                  pid file of the VM's passt
    net.sock                   passt, the VM's network
    qmp.sock                   QMP
    serial.sock                serial console
    console.log                everything the guest printed on ttyS0
    qga.sock                   qemu-guest-agent
  ```

- A base image is downloaded once per digest and shared by every VM that
  uses it. It streams into a 0600 part file and is hashed on the way; a
  wrong digest, a body over `JATSZOTER_AGENT_IMAGE_LIMIT_GIB` (default 20),
  a download past `JATSZOTER_AGENT_IMAGE_DOWNLOAD_MINUTES` (default 60) or
  any other failure removes the part file. The URL and every redirect
  must use https unless they point to loopback, and must pass the address
  rule in [06](06-security.md#image-downloads).
- The cache holds at most `JATSZOTER_AGENT_IMAGE_CACHE_GIB` (default 100,
  at least `JATSZOTER_AGENT_IMAGE_LIMIT_GIB`). Before a download it reserves
  the image's `Content-Length`, or the per-image limit when the server sends
  none, and evicts the least recently used images that no deployment of the
  agent names. When the images in use leave no room, the download fails, and
  the agent logs "the image cache is full".

- QEMU is daemonized with a pid file, so a VM outlives an agent restart and
  reconciliation adopts it. It runs with KVM. Without a usable `/dev/kvm`
  the start fails, unless `JATSZOTER_AGENT_ALLOW_TCG` (default `false`)
  allows TCG, which is many times slower. See
  [06](06-security.md#guest-and-host).
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
  while the pid in `qemu.pid` runs a command line with `-name <vm_id>`
  exactly, so a reused pid is never taken for it. Destroy asks QEMU to quit over QMP, kills
  it if it does not, and removes the directory.
- `created_at` in `vm.json` is the VM's creation time in unix seconds.
- The agent packs the seed into `seed.iso` with `xorriso -as mkisofs`,
  volume label `cidata`, from `JATSZOTER_AGENT_XORRISO` (default `xorriso`),
  spawned with an argument vector like `qemu-img`. The file is 0600 and
  holds the seed of [03](03-scenario-format.md#bootstrap).
- QEMU attaches `seed.iso` read-only as a CD-ROM. After the marker says
  `ok`, the agent ejects it through QMP and deletes the file.
- Network is NAT through [passt](https://passt.top/), one unprivileged
  process per VM from `JATSZOTER_AGENT_PASST` (default `passt`). The agent
  starts it before QEMU, and QEMU joins it over `net.sock` with
  `-netdev stream`, so QEMU 7.2 or newer is required.
- The guest sees `10.0.2.15/24`, gateway `10.0.2.2` and DNS `10.0.2.3`,
  IPv4 only. passt forwards DNS to the host's first resolver and opens
  every other connection as an ordinary socket of the agent's user. The
  guest reaches the internet for package installs; nothing on the network
  reaches the guest. The one way in is from the agent host's own `127.0.0.1`,
  for the ports a scenario's network probes name, see
  [03](03-scenario-format.md#network-probes).
- passt runs with `--no-map-gw`: the gateway does not lead to the host's
  loopback, unlike QEMU's own user-mode network. It runs with `--one-off`
  and exits when QEMU does; destroy also kills the pid in `passt.pid` when
  its command line names the VM's `net.sock`.
- passt must be `2026_10_02` or newer, and the agent refuses to start with
  an older one or as root. `jatszoter-agent check-network` starts passt as
  above, plays the guest and fails when the host is not safe.
  Installing it is [09](09-guest-network.md).
- The trainee's shell is the guest's serial console `ttyS0`; no SSH and no
  port forwarding are involved in it.
- QEMU logs the serial console to `console.log` from boot; the agent ships
  that file to the API, see [04](04-console.md#pipeline).
- Work inside the guest - bootstrap status, checks, terminal resize - goes
  through qemu-guest-agent `guest-exec` and `guest-file-*`, never through
  the console.
- A disk check reads the stopped VM's disk in an inspector VM with no network,
  from the image at `JATSZOTER_AGENT_INSPECTOR_URL`; see
  [03](03-scenario-format.md#disk-checks).
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
   stores a Check `pending`. The deployment must be READY, and one check
   is pending at a time: a request while one is answers 409.
2. The agent sees the check's id in its desired state, fetches the script,
   writes it into the guest, runs it with the scenario's `check_seconds`
   timeout and deletes it. When the script passed, it asks the scenario's
   network probes from the agent host.
3. The agent posts the status, the exit code, the first stdout line and
   the start of stderr; the API stores the result, shows it and writes it
   into the recording as a marker, see [05](05-recording.md#format).
4. A check the agent never answers is settled by the API: once it is
   pending longer than `check_seconds` plus 30 seconds, the next request
   or read of the deployment's checks stores it as `timeout`. A result
   that arrives after that gets 409 and changes nothing.
5. A deployment that ends settles its pending check as `error` with the
   message `deployment ended`.

Stop:

1. Operator stops the deployment; STARTING, BOOTSTRAPPING and READY move
   to STOPPING, PENDING to CANCELLED.
2. When the scenario has disk checks and the deployment reached READY, the
   agent powers the VM off, reads its disk in an inspector VM and posts the
   verdict; see [03](03-scenario-format.md#disk-checks).
3. The agent destroys the VM and its directory, then moves it to COMPLETED.
4. COMPLETED revokes the deployment's links and closes the trainee link's
   recording: the API stores its final size and duration. FAILED and
   CANCELLED do the same.

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
  transition conflicts. A failed start moves it to FAILED and leaves no VM
  directory behind. Its reason is a stable code and an error id, such as
  `vm-failed (error <uuid4>)`, with the code `image-failed`, `seed-failed`,
  `vm-failed` or `agent-failed`. The agent logs the error itself under that
  id, so tool output and host paths never reach the operator.
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
