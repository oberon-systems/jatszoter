# 02 - Domain model

The Deployment is the primary runtime entity; the VM is an implementation
detail of the agent. This document lists the entities the API stores, their
identifiers and the deployment lifecycle.

- [Identifiers](#identifiers)
- [Entities](#entities)
- [Deployment lifecycle](#deployment-lifecycle)
- [Who moves what](#who-moves-what)
- [Acceptance criteria](#acceptance-criteria)

## Identifiers

Every id is a prefix and 32 lowercase hex characters, assigned by the API.
Clients never send an id or a status on create.

| Entity      | Id field        | Example prefix |
| ----------- | --------------- | -------------- |
| Agent       | `agent_id`      | `agt_`         |
| Lease       | `lease_id`      | `lse_`         |
| Image       | `image_id`      | `img_`         |
| Scenario    | `scenario_id`   | `scn_`         |
| Deployment  | `deployment_id` | `dep_`         |
| VM          | `vm_id`         | `vm_`          |
| Share link  | `share_id`      | `shr_`         |
| Session     | `session_id`    | `ses_`         |
| Recording   | `recording_id`  | `rec_`         |
| CheckResult | `check_id`      | `chk_`         |

## Entities

- **Agent** - a registered host: name, placement labels, reported capacity
  N, token hashes, revoked flag.
- **Lease** - the agent's single live lease: `expires_at`, renewed by
  heartbeat. A heartbeat after expiry opens a new lease with a new id.
- **Image** - a base disk registered by the operator: name as used in
  `scenario.yaml`, URL, sha256 digest. Images must ship cloud-init and
  qemu-guest-agent.
- **Scenario** - one registered version of a scenario directory: the slug
  from `scenario.yaml`, `version`, the parsed document and the content of
  every referenced file. A registered version is immutable; a change is a
  new version.
- **Deployment** - scenario version, agent and lease, status, reason,
  timestamps, `vm_id`, and the resources and timeouts copied from the
  scenario at create time.
- **ShareLink** - token hash, deployment, `expires_at`, `revoked_at`. See
  [04](04-console.md#share-links).
- **Session** - one trainee's time on a deployment: share link, opened and
  closed times, who holds control. It owns one recording.
- **Recording** - asciicast file path, size, duration, session.
- **CheckResult** - deployment, session if a trainee ran it, who requested
  it, status (`pending`, `passed`, `failed`, `error`, `timeout`), exit code,
  message, times.

## Deployment lifecycle

```text
 PENDING -> STARTING -> BOOTSTRAPPING -> READY -> STOPPING -> COMPLETED
    |          |             |            |          |
    v          +-------------+------------+----------+--> FAILED
 CANCELLED
```

- Any non-terminal state may move to FAILED, always with a reason.
- Stopping a PENDING deployment moves it to CANCELLED.
- A READY deployment past `session_minutes` moves to STOPPING with reason
  `expired`.
- BOOTSTRAPPING past `bootstrap_seconds` moves to FAILED with reason
  `bootstrap timeout`.
- COMPLETED, FAILED and CANCELLED are terminal; nothing leaves them.
- Share links and checks are accepted only while the deployment is READY.

## Who moves what

| From          | To            | Actor    | Trigger                        |
| ------------- | ------------- | -------- | ------------------------------ |
| PENDING       | STARTING      | agent    | assigned by heartbeat          |
| PENDING       | CANCELLED     | operator | stop                           |
| STARTING      | BOOTSTRAPPING | agent    | guest agent answers            |
| BOOTSTRAPPING | READY         | agent    | bootstrap marker `ok`          |
| READY         | STOPPING      | operator | stop                           |
| READY         | STOPPING      | api      | session TTL expired            |
| STOPPING      | COMPLETED     | agent    | VM destroyed                   |
| non-terminal  | FAILED        | agent    | runtime or bootstrap error     |
| non-terminal  | FAILED        | api      | lease expired, timeout         |

Every write is compare-and-swap on the current status, and for agent writes
on the lease as well.

## Acceptance criteria

- The lifecycle matches `AGENTS.md` exactly.
- Every transition has one actor and one trigger; any other pair gets 409.
- No entity other than Scenario and Image outlives the need for it without
  an explicit retention rule in [05](05-recording.md) or
  [07](07-roadmap.md).
