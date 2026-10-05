# 07 - Smoke: a Rocky 9 deployment reaches READY

Read `promts/00-README.md` first; its ground rules apply. Then read
`make/tests/smoke.sh`, `make/tests/smoke.mk` and Bootstrap in
`docs/project/03-scenario-format.md`.

## Goal

The smoke stand boots the sample scenario on a real Rocky Linux 9
GenericCloud image: the deployment goes through BOOTSTRAPPING to READY, the
seed is ejected, and a stop destroys it as before.

## Established facts

- `smoke.sh` serves `$TEMP_DIR/www/` on `127.0.0.1:8081`, logs in as the
  operator, registers a blank 64M disk as `rocky-9` and `nginx-502` by URL,
  deploys it, waits for STARTING and a live `qemu.pid`, stops it and waits
  for COMPLETED, the pid gone and the directory removed. `cleanup` kills
  QEMU by the pid files.
- The API runs from `$TEMP_DIR`, so its file store goes with the run.
- `nginx-502` version 2 asks for `disk_gib: 12` and
  `bootstrap_seconds: 1800`.
- The agent needs `xorriso` since prompt 05.

## Scope

`make/tests/smoke.sh` and `make/tests/smoke.mk`.

- `smoke.mk` passes three variables, overridable from the command line:
  - `SMOKE_IMAGE_URL`: a versioned Rocky 9 GenericCloud Base qcow2 on the
    official download site - a dated file name, never one named `latest`;
  - `SMOKE_IMAGE_SHA256`: its sha256 from the `CHECKSUM` file published
    next to it;
  - `SMOKE_CACHE`: default `${XDG_CACHE_HOME:-$HOME/.cache}/jatszoter/smoke`.
- Tool check up front gains `xorriso` and `sha256sum`.
- Image: if the cached file is missing or its sha256 differs, download it
  with `curl -fL --retry 3` into a `.part` file next to it, verify, rename;
  a mismatch after download fails with one clear line and removes the part.
  Link the cached file into `$TEMP_DIR/www/rocky-9.qcow2` and register that
  URL with `SMOKE_IMAGE_SHA256` as image `rocky-9`. The blank disk goes.
- Echo whether the run has KVM (`/dev/kvm` readable and writable) or
  TCG, and that the boot takes minutes.
- After the deployment: wait for READY within the deployment's
  `bootstrap_seconds` plus 120 s; FAILED stops the wait at once and prints
  the reason. At READY, `qemu.pid` is alive and `seed.iso` is gone from the
  VM directory.
- Stop and the checks after it are unchanged.

## Failure behaviour

Any stage that times out names the stage and exits non-zero; `cleanup`
still runs; no QEMU survives the script. A failed download leaves no part
file in the cache.

## Acceptance

- `make smoke` passes with KVM, and without it given the time.
- After a forced failure (for example `exit 3` added to `break.sh`, tried
  locally and reverted) the deployment ends FAILED with reason
  `failed break 3`, the script exits non-zero, and nothing from the run is
  left running.
- `.venv/bin/pre-commit run --files make/tests/smoke.sh make/tests/smoke.mk`
  passes.
