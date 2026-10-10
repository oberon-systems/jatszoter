# 05 - Smoke: a session leaves a recording that replays

Read `promts/00-README.md` first; its ground rules apply. Then read
`make/tests/smoke.sh`, `make/tests/smoke.mk` and
`docs/project/05-recording.md` in full.

## Goal

The smoke stand proves item 9 end to end: the session the `terminal` step
drives is found as a recording, the file is a valid asciicast that holds
what the trainee's terminal showed, and it survives the deployment.

## Established facts

- `open_link` opens the trainee link after READY, so the recording starts
  with the bytes since the READY offset.
- `terminal` types three lines, takes control between the first and the
  second, and sends one resize of 100 by 30 as the operator.
- `signed` makes an operator request and can write the body to a file, as
  the console download does.
- The API's file store is `data/storage` under `$TEMP_DIR`.

## Scope

`make/tests/smoke.sh` only.

- After `terminal` and before the revoke: read the recording's id from a
  signed `GET /api/v1/deployments/$deployment/recordings`, which must list
  exactly one, and download it with a signed `GET
  /api/v1/recordings/$recording.cast` into `$TEMP_DIR`.
- A `replay` step, one Python one-shot given the file's path and the texts
  to look for through its environment, checks in order:
  - the first line is a header with `version` 2 and integer `width` and
    `height`;
  - every other line is a three-item array of a number, one of `o`, `r`,
    `m`, and a string, and the numbers never decrease;
  - the joined `o` text holds the expanded text of the line the trainee
    typed and of the line the operator typed, and not that of the line
    typed while the trainee was cut;
  - an `m` event `control operator` sits between those two texts;
  - an `r` event `100x30` is present.
  It prints the name of each check as it passes and never the content.
- After the revoke: the listing shows a size above zero and a duration
  above zero for the recording.
- After the stop and COMPLETED: the download answers the same number of
  bytes or more than before the revoke, and the file exists under
  `data/storage/recordings/$deployment/`.
- The recording joins `api.log` and `agent.log` in the search for the link
  token and the cookie value.

## Failure behaviour

Any check that fails names itself and exits non-zero; `cleanup` still
runs; no QEMU survives the script. The key never appears on a command
line, and no line of the recording is printed.

## Acceptance

- `make smoke` passes with KVM.
- With the recording's output call removed from `ship` (tried locally and
  reverted), the smoke run fails at the typed text.
- `.venv/bin/pre-commit run --files make/tests/smoke.sh` passes.
