# 01 - Docs: write the item 4 contracts

Read `promts/00-README.md` first; its ground rules apply. If the
`write-docs` skill is available, use it; markdownlint in pre-commit gates the
result either way.

## Goal

`AGENTS.md` says a change of design is proposed in `docs/` before code. This
prompt changes documentation only, no code. Later prompts implement exactly
what is written here, so be precise about routes, fields and states.

## Files

`AGENTS.md`, `docs/project/01-architecture.md`, `02-domain-model.md`,
`03-scenario-format.md`, `05-recording.md`, `06-security.md`,
`07-roadmap.md`. Nothing else.

## Changes

### Lifecycle (`AGENTS.md`, `02-domain-model.md`)

- The operator stops a STARTING, BOOTSTRAPPING or READY deployment: it moves
  to STOPPING. PENDING still moves to CANCELLED. Update the lifecycle text in
  `AGENTS.md`, the diagram if needed, and the "Who moves what" table in 02.
- Deployment gains `image_id`, copied at create time like the resources.

### Entities (`02-domain-model.md`)

- User: `password_hash` (scrypt, parameters stored with the hash) and
  `password_change_required`.
- Image: registered once; the same name with a different URL or digest is
  refused. Digest is 64 lowercase hex characters of sha256.
- Scenario: slug, version, the parsed `scenario.yaml` document, the bundle's
  file-store URL and its digest. File contents are no longer SQL columns.
- Deployment: note that trainee links arrive with item 8.

### Architecture (`01-architecture.md`)

- New section "Operator API" listing:

  ```text
  POST /api/v1/auth/login                    open
  POST /api/v1/auth/credential               signed (user)
  POST /api/v1/images                        signed (operator)
  GET  /api/v1/images                        signed (operator)
  POST /api/v1/scenarios                     signed (operator)
  GET  /api/v1/scenarios                     signed (operator)
  POST /api/v1/deployments                   signed (operator)
  GET  /api/v1/deployments                   signed (operator)
  GET  /api/v1/deployments/{id}              signed (operator)
  POST /api/v1/deployments/{id}/stop         signed (operator)
  ```

  A user request carries the owner id in `X-Owner`, the rest of the
  credential and signature exactly like an agent request.
- Agent protocol:
  - a heartbeat assigns PENDING deployments to the agent: first those the
    operator pinned to it, then unpinned ones, oldest first, up to its
    capacity minus the non-terminal deployments it already holds. The
    assignment is compare-and-swap on `agent_id` and sets `vm_id`;
  - signed GET requests sign an empty body;
  - one desired-state entry is: `id`, `vm_id`, `status`, `scenario`
    (`id`, `slug`, `version`), `image` (`name`, `url`, `digest`),
    `resources` (`cpus`, `memory_mib`, `disk_gib`), `timeouts`
    (`bootstrap_seconds`, `check_seconds`, `session_minutes`); a request
    without a live lease gets 409;
  - a transition body is `event`, `expected` (the status the agent believes),
    optional `reason`; the agent events are `assign`, `boot`, `bootstrap`,
    `destroy`, `fail`.
- VM runtime: under the agent's state directory, `images/<digest>.qcow2` and
  `vms/<deployment_id>/` holding `disk.qcow2`, `vm.json`, `qemu.pid`,
  `qmp.sock`, `serial.sock`, `qga.sock`. QEMU is daemonized with a pid file,
  so VMs outlive an agent restart and reconciliation adopts them. KVM when
  `/dev/kvm` is usable, TCG otherwise.
- Data flows, Stop: STARTING, BOOTSTRAPPING and READY move to STOPPING.
- Stack, Files: SeaweedFS filer at `JATSZOTER_SEAWEED_URL`; without it the
  API writes the same layout under `data/storage/` in its working directory.

### Scenario format (`03-scenario-format.md`)

- Registering: a signed operator `POST /api/v1/scenarios`, either multipart
  with a `bundle` field (tar.gz of the scenario directory) or JSON
  `{"url": "https://example.com/nginx-502.tar.gz"}` that the API fetches.
  Replace the Bearer token example.
- Fetch rules: https, or http to loopback; no redirects; size limit
  `JATSZOTER_SCENARIO_BUNDLE_LIMIT_BYTES` (10 MiB default); 30 s timeout.
- Bundle rules: regular files only, no links, no absolute paths or `..`;
  files at the root or under one top directory named like the `id`.
- The bundle is stored as `scenarios/<slug>/<version>.tar.gz` in the file
  store. The digest is sha256 over the sorted file names and contents, so
  re-packing the same files is still the same content.
- The referenced image must already be registered.
- The schema is part of the API version and moves to
  `packages/api/src/jatszoter_api/schemas/scenario.schema.yaml`; update the
  path in the text.

### Recording storage (`05-recording.md`)

Replace "an in-process mock of it" with `data/storage/` under the API's
working directory, used when `JATSZOTER_SEAWEED_URL` is unset.

### Security (`06-security.md`)

- Operator: logs in with name and password and gets a credential and signing
  key, like an agent's claim. On first start, when no operator exists, the API
  creates `admin` / `admin` with `password_change_required`; such a login is
  refused until it sends `new_password`.
- Passwords are stored as scrypt hashes. Unknown user and wrong password get
  the same 401. Ten failures for one name within 15 minutes get 429 until the
  window passes; the counter lives in Valkey.
- Scenario URLs are fetched by the API: only operators can name one, and the
  fetch rules above bound it.

### Roadmap (`07-roadmap.md`)

Rewrite item 4 as:

```text
4. Operator login, images, scenario registry and the QEMU runtime.
   - API: user login and signed operator requests; images; scenarios from
     a bundle or a URL, stored in SeaweedFS or a local directory;
     deployments created, assigned and stopped.
   - Agent: desired state with image URL and digest, image cache, VM create
     and destroy, reconcile.
   - Smoke: log in, register the sample scenario by URL, deploy it; the
     deployment is assigned, started and destroyed on stop.
```

Item 5 becomes "Merged into 4." with its number kept, so later numbers do
not move.

## Acceptance

- `.venv/bin/pre-commit run --files` on every edited file passes.
- Every route, field, setting and state named above appears in the docs,
  and nothing contradicts it elsewhere in `docs/` or `AGENTS.md`
  (`grep -rn "READY -> STOPPING\|in-process mock\|Bearer" docs AGENTS.md`).
