# 02 - Domain model

The Deployment is the primary runtime entity; the VM is an implementation
detail of the agent. This document lists the entities the API stores, their
identifiers and the deployment lifecycle.

- [Identifiers](#identifiers)
- [Entities](#entities)
- [Deployment lifecycle](#deployment-lifecycle)
- [Who moves what](#who-moves-what)
- [Storage rules](#storage-rules)
- [Ephemeral state](#ephemeral-state)
- [Row cache](#row-cache)
- [Acceptance criteria](#acceptance-criteria)

## Identifiers

Every id is a random UUID (version 4) with no prefix, assigned by the API.
Clients never send an id or a status on create. A reference to another
entity is a column named after it, such as `deployment_id` or `user_id`,
holding that entity's UUID.

## Entities

- **User** - a person: name, `role` (`operator`, `trainee` or `viewer`,
  used like a group), comment, `state` (`disabled` until an operator enables
  it, like an agent). The role says what every link of that user can do. A
  user owns a secret.
- **Agent** - a registered host: name, placement labels, `state`
  (`disabled` while it waits for an operator or once one turns it off,
  `enabled` after approval). An agent owns a secret. Its last heartbeat and
  reported capacity N are [ephemeral state](#ephemeral-state).
- **Secret** - one row per owner, an agent or a user: `owner_id`, the live
  `generation` and a `pending_generation` during a refresh. No key is
  stored; signing keys are derived from a master key, and the credential
  that names them knows itself whether it is due for refresh or expired.
  See [06](06-security.md#credentials).
- **Lease** - the agent's single live lease: its id, the reported capacity N,
  the last heartbeat and the deadline, ephemeral state renewed by heartbeat.
  A heartbeat after expiry opens a new lease with a new id.
- **Image** - a base disk registered by the operator: name as used in
  `scenario.yaml`, URL, sha256 digest. Images must ship cloud-init and
  qemu-guest-agent.
- **Scenario** - one registered version of a scenario directory: the slug
  from `scenario.yaml`, `version`, the parsed document and the content of
  every referenced file. A registered version is immutable; a change is a
  new version.
- **Deployment** - scenario version, agent, status, reason, timestamps,
  `vm_id`, and the resources and timeouts copied from the scenario at create
  time.
- **Link** - deployment, user, token hash, `opened_at` on first open,
  `revoked_at`. The link acts with its user's role; a trainee's link owns one
  recording. A deployment is created with its trainee link, and ending
  the deployment revokes all its links. See [04](04-console.md#links).
- **Recording** - URL of the asciicast in object storage, size, duration,
  trainee link.
- **Check** - deployment, the link it came through if any, the user who
  requested it, status (`pending`, `passed`, `failed`, `error`, `timeout`),
  exit code, message, times.

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
- Links open the terminal and checks are accepted only while the deployment
  is READY.

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
on `agent_id` as well, under a live lease.

The table lives in `jatszoter_api.lifecycle` as a
[python-statemachine](https://github.com/fgmacedo/python-statemachine)
machine: one event per row, each carrying the condition on its actor.

## Storage rules

The API runs on any SQL database SQLModel can reach; no database product is
named or special-cased anywhere in the code.

- Models are plain SQLModel; Alembic is used for migrations and nothing else.
- No foreign keys, stored procedures, triggers or server-side defaults.
  References are indexed id columns, and the API keeps them consistent.
- Enums are stored as strings, documents as JSON columns, times as integer
  seconds since the epoch in UTC.
- Recording files never go into SQL: they live in SeaweedFS and the row
  keeps their URL, see [05](05-recording.md#storage).
- Nothing that lives by a TTL is a SQL row; see below.

## Ephemeral state

State that expires on its own lives in [Valkey](https://valkey.io/) with a
TTL, and never in SQL. When `JATSZOTER_VALKEY_DSN` is unset - in smoke runs
and tests always - the API uses an in-memory
[fakeredis](https://github.com/cunla/fakeredis-py) instead.

- The agent's lease under `lease:<agent id>`: its id, the reported capacity N
  and the last heartbeat, written again with the lease TTL by every
  heartbeat.
- A pending registration under `registration:<agent id>`, and its claim code
  under `claim:<sha256 of the code>`, until an operator approves it and the
  agent claims it, or their TTL runs out.

No SQL row names a lease: an expired id would only go stale there. A
deployment names its agent, an agent write is accepted only while Valkey holds
a lease for that agent, and a lapsed lease fails the agent's running
deployments before a new one is granted.

## Row cache

The same Valkey, or fakeredis without a DSN, caches the few values the hot
path reads on every request; SQL stays the source of truth.

- Every model inherits `Cached`: `_get`, `_set`, `_ttl` and `_delete` on a
  full key. `Base` overrides them to keep entries under `<key>:<id>`, ten
  seconds by default; its `_get` renews a hit and, given a loader, stores
  what the loader reads on a miss. A child overrides them again or reaches
  the plain ones through `super()`.
- Only an owner's secret is cached today, under `secret:<owner id>`, for
  `verify`. Every refresh and every promoted generation drops it, so a
  superseded generation is never taken for the live one. Nothing scans the
  cache.

## Acceptance criteria

- The lifecycle matches `AGENTS.md` exactly.
- Every transition has one actor and one trigger; any other pair gets 409.
- No entity other than Scenario and Image outlives the need for it without
  an explicit retention rule in [05](05-recording.md) or
  [07](07-roadmap.md).
