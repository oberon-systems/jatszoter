# 01 - Docs: write the item 6 contracts

Read `promts/00-README.md` first; its ground rules apply. If the
`write-docs` skill is available, use it; markdownlint in pre-commit gates the
result either way.

## Goal

`AGENTS.md` says a change of design is proposed in `docs/` before code. This
prompt changes documentation only, no code. Later prompts implement exactly
what is written here, so be precise about routes, fields, paths and states.

## Files

`docs/project/01-architecture.md`, `02-domain-model.md`,
`03-scenario-format.md`, `06-security.md`, `07-roadmap.md`. Nothing else.

## Changes

### Agent protocol (`01-architecture.md`)

- Add to the route list:

  ```text
  GET  /api/v1/agents/{agent_id}/deployments/{id}/seed       signed
  ```

- The seed is `{"user_data": "...", "meta_data": "..."}`, the two NoCloud
  files as text. The API renders them from the stored bundle of the
  deployment's scenario version (see 03 Bootstrap).
- It is served only for a deployment the agent holds, only while that
  deployment is STARTING, and only under a live lease: 409 otherwise; an
  unknown deployment, or another agent's, is 404.

### VM runtime (`01-architecture.md`)

- The directory listing gains `seed.iso`, the cloud-init NoCloud seed,
  removed once it is ejected; `vm.json` holds `deployment_id`, `vm_id` and
  `created_at` (unix seconds).
- QEMU attaches `seed.iso` read-only as a CD-ROM. After the marker says
  `ok`, the agent ejects it through QMP and deletes the file.
- The agent packs the ISO with `xorriso -as mkisofs`, volume label
  `cidata`, from `JATSZOTER_AGENT_XORRISO` (default `xorriso`).
- Replace the bullet saying the seed ISO joins the VM directory with item 6.

### Data flows (`01-architecture.md`)

Create and bootstrap becomes:

1. Operator creates the deployment (unchanged).
2. A heartbeat assigns it; the agent moves it to STARTING, fetches the image
   and the seed, packs the seed ISO and boots the VM.
3. Once the guest agent answers `guest-ping`, the agent moves the
   deployment to BOOTSTRAPPING.
4. When the marker reads `ok`, the agent ejects the seed ISO and moves the
   deployment to READY. A marker `failed <step> <exit code>` moves it to
   FAILED with the marker as reason; so does `bootstrap_seconds` passing,
   with reason `bootstrap timeout`.

### Reconciliation (`01-architecture.md`)

- A live VM whose deployment is STARTING or BOOTSTRAPPING is probed once per
  round: a short task pings the guest agent or reads the marker and checks
  the deadline, then ends. Nothing waits for a guest in a long task, so a
  stop is never held up by a bootstrap.
- The deadline counts from `created_at` in `vm.json`, so an agent restart
  neither resets nor loses it.
- A deployment the API moved to STOPPING while a start task for it is still
  running has that task aborted; the stop then destroys whatever it left.

### Bootstrap (`03-scenario-format.md`)

Keep the six steps and state how they are carried out:

- `user-data` is a `#cloud-config` document. `meta-data` holds
  `instance-id: <vm_id>` and `local-hostname: <scenario id>`.
- The trainee user is created through cloud-config `users`, with
  passwordless sudo when `sudo` is true, and no password.
- `packages` always gains `qemu-guest-agent`. On RHEL-family guests the
  seed also allows the guest agent's `guest-file-*` and `guest-exec*` calls,
  which the distribution blocks by default, and restarts it.
- The step scripts and one driver script are written under
  `/var/lib/jatszoter/`, mode 0700, owned by root. `runcmd` runs the driver,
  which runs `install`, `configure`, `break` in order with output to
  `/var/lib/jatszoter/bootstrap.log`, stops at the first failure, removes
  the scripts, the log and cloud-init's copies of `user-data`, sets up the
  serial getty, and writes the marker last.
- The marker is `/var/lib/jatszoter/marker`: `ok`, or
  `failed <step> <exit code>`. The agent reads it with `guest-file-open`,
  `guest-file-read` and `guest-file-close`.
- The getty on `ttyS0` logs in as the trainee user without a password and
  clears the screen.
- Add the trainee fallbacks to the settings table:
  `trainee.user` from `JATSZOTER_DEFAULT_TRAINEE_USER` (default `trainee`),
  `trainee.sudo` from `JATSZOTER_DEFAULT_TRAINEE_SUDO` (default `false`).
- Acceptance criteria: `scenarios/nginx-502` bootstraps to READY on a stock
  Rocky Linux 9 GenericCloud image registered as `rocky-9`, not `debian-12`.

### Domain model (`02-domain-model.md`)

Only where it names `debian-12` or the bootstrap trigger: the "Who moves
what" rows stay, but STARTING to BOOTSTRAPPING reads "guest agent answers
`guest-ping`" and BOOTSTRAPPING to READY reads "marker `ok`, seed ejected".

### Security (`06-security.md`)

- Guest and host: the seed enables the guest agent's file and exec calls;
  that is no loss, since the guest is not a security boundary.
- Scenario secrecy: the seed route answers only the agent holding the
  deployment, only while it is STARTING; the agent never logs a seed, keeps
  `seed.iso` 0600 inside the VM directory and deletes it after the eject.

### Roadmap (`07-roadmap.md`)

Rewrite item 6 as:

```text
6. cloud-init bootstrap and the bootstrap marker.
   - API: the seed route renders user-data and meta-data from the stored
     bundle.
   - Agent: fetch the seed, pack, attach and eject the seed ISO, probe the
     guest agent and the marker, fail on the marker or the deadline.
   - Scenario: nginx-502 version 2 on rocky-9.
   - Smoke: a Rocky 9 deployment reaches READY and is destroyed on stop.
```

## Acceptance

- `.venv/bin/pre-commit run --files` on every edited file passes.
- Every route, field, path, setting and state named above appears in the
  docs, and nothing contradicts it elsewhere in `docs/` or `AGENTS.md`
  (`grep -rn "debian-12\|joins the VM directory" docs AGENTS.md` finds
  nothing stale).
