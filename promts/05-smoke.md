# 05 - Smoke: a check fails before the fix and passes after it

Read `promts/00-README.md` first; its ground rules apply. Then read
`make/tests/smoke.sh`, `scenarios/nginx-502/` and the Check parts of
`docs/project/`.

## Goal

The smoke stand proves item 10 end to end on the sample scenario: its
check fails on the broken guest, passes once the guest is repaired, and
both results sit in the recording.

## Established facts

- `break.sh` moves the app unit to port 8018; `check.sh` prints `nginx
  still does not serve the app page` and exits 1 until nginx serves the
  app, then prints `solved`.
- `terminal` ends with the operator holding control; its `Terminal.prints`
  types a line and waits for a text.
- The trainee's cookie is in `$cookie`; a cookie request without an
  `Origin` header is not refused.
- `replay` checks the recording downloaded before the revoke.

## Scope

`make/tests/smoke.sh` only.

- A `check` helper: request a check, poll it until it is no longer
  `pending` within the deployment's `check_seconds` plus a minute, and
  print its status and nothing else. The first one is requested with the
  trainee's cookie, read on stdin or through a curl config, never a
  command line.
- Before the fix, after `terminal`: the check is `failed`, its message is
  the script's first line, and the trainee's answer has no `detail` key.
- The fix is typed through the terminal by the party holding control:
  the app unit back to port 8081, a daemon reload, a restart. The step
  waits for a text only the guest prints.
- After the fix: a check requested by the signed operator is `passed`
  with the message `solved`.
- `replay` also checks that a `check failed` marker comes before a `check
  passed` marker, both after the control marker.
- A second request while one is pending answers 409: ask twice in a row
  once and expect it.

## Failure behaviour

Any check that fails names itself and exits non-zero; `cleanup` still
runs; no QEMU survives the script. The key and the cookie never appear on
a command line, and no stdout or stderr of the check script is printed
beyond its one-line message.

## Acceptance

- `make smoke` passes with KVM.
- With the fix step removed (tried locally and reverted), the smoke run
  fails at the passed check.
- `.venv/bin/pre-commit run --files make/tests/smoke.sh` passes.
