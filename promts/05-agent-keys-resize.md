# 05 - Agent: keys and resize into the guest

Read `promts/00-README.md` first; its ground rules apply. Then read
Pipeline and Resize in `docs/project/04-console.md`, VM runtime in
`docs/project/01-architecture.md`, and naos's `Keyboard` in
`naos/packages/runner/src/libs/console/live.rs`, the model for the keys.

## Goal

What the API sends down the console socket reaches the guest: keys are
written to the serial socket, a resize is applied with `stty` through the
guest agent. Shipping the log is never held up by either for long.

## Established facts

- `console::ship(url, auth, deployment_id, log)` reads `{"offset": n}`
  from text frames into `Reply`, ignores other fields and every binary
  frame, and returns whether the API closed with 4413.
- QEMU serves `serial.sock` to one client at a time and sends it
  everything the guest prints; a client that does not read fills the
  socket and stalls the guest's serial output.
- `Qga::connect(path, timeout)` syncs with the guest agent and `execute`
  sends one command; `qga.sock` is used by the probe only before READY.
- `guest-exec` takes `path`, `arg` and `capture-output` and returns a
  `pid`; `guest-exec-status` takes the `pid` and returns `exited` and
  `exitcode`.
- Prompt 01 proved `stty -F /dev/ttyS0 cols N rows M` works through
  `guest-exec` in the Rocky 9 guest.
- `libs/runtime` calls `ship` with `<vm dir>/console.log`.

## Scope

- `libs/vm`: `SERIAL` is pub.
- `libs/qga`: `exec(argv, wait)` runs `guest-exec` without captured
  output and polls `guest-exec-status` until the process exits or `wait`
  passes; it returns the exit code.
- `libs/console`:
  - `ship(url, auth, deployment_id, dir)` takes the VM directory and finds
    `console.log`, `serial.sock` and `qga.sock` in it;
  - a binary frame is keys: the first one connects `serial.sock`, which is
    then kept until `ship` returns, with a task that reads and discards
    what the guest sends there; the task is aborted with the connection;
  - a write that fails drops the connection, so the next key connects
    again;
  - a text frame with `cols` and `rows`, both 1 to 500, is a resize: when
    it differs from the last size applied, `stty -F /dev/ttyS0 cols N rows
    M` runs through `Qga::exec` within 5 s as an argument vector; the size
    is remembered only after exit code 0.
- `libs/runtime`: passes the VM directory.

## Failure behaviour

- Keys that cannot be written are lost; the log line names the deployment
  and the error, never the bytes or their count.
- A resize that fails or times out is logged and tried again at the next
  resize frame; shipping pauses for at most those 5 s.
- A size out of range is ignored, as is a text frame without both fields.
- Nothing the API sends reaches a host shell.

## Tests

- `libs/qga/tests.rs`: `exec` sends the argument vector and returns the
  exit code; a process that never exits is an error after `wait`.
- `libs/console/tests.rs`, with a `UnixListener` as `serial.sock` and a
  fake guest agent as `qga.sock` in the VM directory:
  - positive: keys from two frames arrive in order on one connection;
    what the serial socket writes back is drained while frames keep
    shipping; a resize runs `stty` with the size as separate arguments; a
    connection that closed is opened again by the next key;
  - negative: without `serial.sock` the keys are lost and the log still
    ships; the same size twice runs `stty` once; a failed `stty` is run
    again by the next frame; `cols` 0 and `rows` 501 run nothing.
- The existing console and runtime tests pass with the new argument.

## Acceptance

`cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D
warnings`, `cargo test -p jatszoter-agent` green; `make smoke` passes;
`.venv/bin/pre-commit run --files` on every edited file passes.
