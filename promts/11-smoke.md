# 11 - Smoke: a deployment is assigned, started and destroyed on stop

Read `promts/00-README.md` first; its ground rules apply. Then read
`make/tests/smoke.sh`, `make/tests/smoke.mk` and the Operator API section of
`docs/project/01-architecture.md`.

## Goal

Extend the smoke stand to run item 4 end to end with real QEMU: log in as
the operator, register an image and the sample scenario by URL, deploy it,
watch the VM start, stop it and watch it go.

## Established facts

- `smoke.sh` starts the API (SQLite, fakeredis, auto registration) and one
  agent in their own process groups under `$TEMP_DIR`, waits for the claim
  and two renewals under one lease id, and cleans up on exit.
- The API creates `admin` / `admin` with a required password change.
- Operator requests carry `X-Owner`, `X-Credential`, `X-Timestamp` and
  `X-Signature` = hex HMAC-SHA256 under the key of
  `ts\nMETHOD\npath\nsha256hex(body)`.
- `scenarios/nginx-502` names the image `debian-12`. In the smoke stand that
  name is bound to a blank disk; nothing boots from it before item 6.
- QEMU daemonizes, so its processes are not in the agent's process group.

## Scope

`make/tests/smoke.sh` only, plus `smoke.mk` if a variable must pass through.
The script is the work itself; no helper scripts.

- Up front, fail with one clear line if `qemu-system-x86_64`, `qemu-img`,
  `openssl`, `jq` or `curl` is missing.
- Build `$TEMP_DIR/www/`: `qemu-img create -f qcow2 base.qcow2 64M` and
  `tar -czf nginx-502.tar.gz -C "$ROOT/scenarios" nginx-502`; serve it with
  `"$VENV/bin/python" -m http.server --bind 127.0.0.1 8081 --directory ...`
  in its own process group, killed by `cleanup`.
- A `signed METHOD PATH [BODY]` function: timestamp, body hash with
  `sha256sum`, signature with `openssl dgst -sha256 -hmac "$key"`, then
  `curl -fsS` with the four headers. The key never reaches the log or the
  terminal.
- Flow after the existing agent checks:
  1. `POST /api/v1/auth/login` as admin: expect 403, then log in with a
     random `new_password`; keep `user_id`, credential and key;
  2. register image `debian-12` with the base URL and its `sha256sum`;
  3. register the scenario by URL; check
     `$TEMP_DIR/files/scenarios/nginx-502/1.tar.gz` exists;
  4. create a deployment of it; wait (120 s) for STARTING and a live
     `qemu.pid` under `$TEMP_DIR/agent/vms/<deployment id>/` (`kill -0`);
  5. stop it; wait (60 s) for COMPLETED, the pid gone and the directory
     removed.
- `cleanup` also kills any pid found in `$TEMP_DIR/agent/vms/*/qemu.pid`
  before removing `$TEMP_DIR`, so a failed run leaves no QEMU behind.
- Echo one line per stage, as the script does now.

## Failure behaviour

Any stage that times out names the stage and exits non-zero; `cleanup` still
runs; no QEMU survives the script.

## Acceptance

- `make smoke` passes on a host with QEMU, with and without access to
  `/dev/kvm`.
- After a forced failure (for example a wrong image digest in step 2, tried
  locally and reverted), the deployment ends FAILED, the script exits
  non-zero, and `pgrep -f jatszoter-agent`, `pgrep qemu-system` find nothing
  left from the run.
- `.venv/bin/pre-commit run --files make/tests/smoke.sh make/tests/smoke.mk`
  passes (shellcheck, shfmt).
