# 06 - Smoke: an operator sees the console

Read `promts/00-README.md` first; its ground rules apply. Then read
`make/tests/smoke.sh`, `make/tests/smoke.mk` and `docs/project/04-console.md`.

## Goal

The smoke stand proves the console pipeline end to end: once the Rocky 9
deployment is READY, an operator attached to it receives console output,
and the downloaded log holds the boot.

## Established facts

- `smoke.sh` logs in as the operator and keeps `owner`, `credential` and
  `key`; `signed` builds the four headers for curl over
  `ts\nMETHOD\npath\nsha256(body)`.
- It waits for READY within `bootstrap_seconds` plus 120 s, checks that
  `qemu.pid` runs and `seed.iso` is gone, then stops the deployment and
  waits for COMPLETED.
- The guest's serial getty logs the trainee in and clears the screen after
  the bootstrap, so the console prints at least a prompt after READY.
- Prompt 04 added `websockets` to `requirements.txt`, so `$VENV/bin/python`
  can open a WebSocket.

## Scope

`make/tests/smoke.sh` only.

- A `viewer` step after the READY checks: a Python one-shot run with
  `$VENV/bin/python -I -c`, given the URL, owner, credential and key
  through its environment, signs the upgrade like `signed` does for `GET`
  and an empty body, attaches to `/api/v1/deployments/$deployment/attach`,
  reads until it has a text `{"offset": ...}` and at least one binary frame
  or 30 s pass, and exits non-zero on a timeout or a refused upgrade. It
  prints byte counts, never the bytes.
- After it: `signed GET /api/v1/deployments/$deployment/console` into a file
  under `$TEMP_DIR`; it must be non-empty and contain `Rocky Linux`, which
  the guest prints during boot.
- `$TEMP_DIR/agent/vms/$deployment/console.log` exists and is non-empty.
- Stop and the checks after it are unchanged.

## Failure behaviour

Any stage that times out names the stage and exits non-zero; `cleanup`
still runs; no QEMU survives the script. The key never appears on a command
line: it reaches the Python step through the environment only.

## Acceptance

- `make smoke` passes with KVM.
- With the agent stopped for 10 s after READY (`kill -STOP`, then `-CONT`,
  tried locally and reverted; the lease lasts 30 s), the viewer still
  receives output and the downloaded log has no gap at the pause.
- `.venv/bin/pre-commit run --files make/tests/smoke.sh` passes.
