# 04 - Agent: run a pending check in the guest

Read `promts/00-README.md` first; its ground rules apply. Then read Agent
protocol, VM runtime and Reconciliation in
`docs/project/01-architecture.md`, Check in
`docs/project/03-scenario-format.md`, and `libs/qga`, `libs/api` and
`libs/runtime` of `packages/agent`.

## Goal

A pending check in the desired state is run inside the guest once, and
its result reaches the API.

## Established facts

- `Desired` in `libs/api` is one desired-state entry; `Seed` is fetched
  by a signed GET and its `Debug` prints no content.
- `Qga::exec` runs an argument vector without capturing output and
  returns the exit code; `Qga::read_file` reads a guest file through
  `guest-file-open`.
- `libs/runtime` runs each action as its own task and never works on a
  deployment twice; a probe is a short task per round.
- The pid `guest-exec` answers is the guest's own.

## Scope

- `libs/api`: `Desired` gains the optional check id; a signed GET for the
  script and a signed POST for the result. The script type prints no
  content in `Debug`.
- `libs/qga`: write a file into the guest, and run an argument vector
  with captured stdout and stderr, answering the exit code and both
  outputs, or that the deadline passed, with the pid.
- A check module, or `libs/runtime` where that is how probes are laid
  out - ask before adding a module: fetch the script, write it under
  `/run` in the guest under a name with the check id, run it with
  `/bin/sh` as the guest agent's user, delete it whatever happened, post
  the result.
- `libs/runtime`: a READY deployment with a check id gets one check task;
  the same check id is never started twice, and a check never delays a
  heartbeat or a stop.
- The result: exit 0 is `passed`, another code `failed`; `message` is the
  first stdout line cut to 200 characters, `detail` the first 4096 bytes
  of stderr.

## Failure behaviour

- Once `timeout_seconds` pass the agent kills the pid in the guest and
  posts `timeout` without an exit code.
- A guest agent that does not answer, a file that cannot be written and
  output too large to read post `error` with a short message that names
  no path of the host.
- The script is deleted from the guest after a pass, a failure, a
  timeout and an error alike; a delete that fails is logged by the check
  id.
- A script answer of 404 or 409 ends the task quietly; a result post of
  409 is not retried.
- The script, stdout and stderr are never logged.

## Tests

`libs/qga/tests.rs`, `libs/api/tests.rs`, the check's own tests, with the
fake guest agent socket the console tests use:

- positive: a script that exits 0 posts `passed` with its first line; one
  that exits 3 posts `failed` with exit code 3 and stderr as detail; the
  file is written before the run and deleted after it;
- negative: a script still running at the deadline is killed and posted
  as `timeout`, and still deleted; a guest agent error posts `error`; the
  same check id in two rounds runs once; a 409 on the script ends without
  a post.

## Acceptance

`cargo test -p jatszoter-agent` and `cargo clippy -p jatszoter-agent`
green; `make smoke` passes; `.venv/bin/pre-commit run --files` on every
edited file passes.
