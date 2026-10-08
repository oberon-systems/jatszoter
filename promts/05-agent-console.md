# 05 - Agent: ship the serial console

Read `promts/00-README.md` first; its ground rules apply. Then read
`docs/project/04-console.md`, VM runtime and Reconciliation in
`docs/project/01-architecture.md`, Credentials in
`docs/project/06-security.md`, and naos's runner console in
`naos/packages/runner/src/libs/console/live.rs` and `ship.rs`, the model
for this prompt.

## Goal

QEMU logs each VM's serial console to `console.log`; the agent holds one
console socket per live VM and ships that file to the API by offset, so
the API holds every byte the guest printed, across agent and API restarts.

## Established facts

- `vm::qemu_args` builds the serial chardev as
  `socket,id=serial0,server=on,wait=off,path=<dir>/serial.sock` and escapes
  commas in paths through `option`.
- QEMU's `logfile=<path>,logappend=on` on a chardev writes everything the
  guest sends to the file, connected client or not.
- `QemuRuntime` keeps short tasks in `tasks` and their kinds in `kinds`;
  `reconcile` runs after every accepted heartbeat with the desired state and
  `vm::list`; `destroy_all` fences.
- Tasks sign with `Auth`, the live credential under a `RwLock`; a task
  holds the read guard only to build one request, bounded by `SIGN_WAIT`
  (15 s), never for a socket's lifetime.
- `api::sign(key, timestamp, method, path, body)` produces `X-Signature`;
  requests carry `X-Credential`, `X-Timestamp` and `X-Signature`.
- `api_url` is https, or http on loopback; the socket URL swaps the scheme
  for wss or ws.
- Prompt 04 answers `{"offset": n}` on connect and after every frame,
  closes 4413 when the log is full, and refuses a deployment that is not
  STARTING, BOOTSTRAPPING or READY.

## Scope

- `Cargo.toml`: `tokio-tungstenite` with rustls and no native-tls, and
  `futures-util` for the stream and sink traits.
- `libs/vm`: the serial chardev gains `logfile=<dir>/console.log` and
  `logappend=on`, the path escaped like the others; `CONSOLE_LOG` is pub;
  the VM directory listing in docs already names the file.
- `libs/console/mod.rs`:
  - `read_at(path, offset, buf) -> io::Result<usize>`: reads from `offset`,
    0 at or past the end and for a missing file;
  - `ship(url, auth, deployment_id, log)`: connects with the three signed
    headers over `GET`, the URL path and an empty body, within 10 s; then
    loops on the socket and a 50 ms ticker: a text `{"offset": n}` sets the
    offset, backwards too, and allows the next frame; while no frame is
    unanswered, the ticker reads up to 64 KiB at the offset and sends
    `offset (u64, big-endian) || bytes`; it returns whether the API closed
    with 4413.
- `libs/runtime`: a `consoles` map beside `tasks`, one long task per
  deployment, outside the busy set and the capacity:
  - each round, a live VM whose deployment is STARTING, BOOTSTRAPPING or
    READY gets a console task when it has none; the task calls `ship`
    again 5 s after every end until it is aborted or the log is full;
  - a full deployment is remembered and not shipped again while it stays in
    the desired state;
  - a console task whose deployment leaves those statuses, or whose VM is
    gone, is aborted at the start of the round, before any Stop runs;
  - `destroy_all` aborts every console task with the others.

## Failure behaviour

- An API that refuses or drops the socket costs a 5 s pause; the bytes wait
  in `console.log`, so none is lost.
- An agent restart starts from offset 0 and the API's first answer moves it
  to what it holds; nothing is sent twice into the full log.
- A refreshed credential is picked up at the next connect; a socket opened
  with the old one stays open until it ends.
- Console bytes never reach a log line; a log line names the deployment and
  the error.

## Tests

- `libs/vm/tests.rs`: the argv carries the logfile on the serial chardev,
  escaped in the comma test.
- `libs/console/tests.rs`, against a local `tokio-tungstenite` server on
  `127.0.0.1:0` standing in for the API:
  - positive: the upgrade carries the three headers and the signature
    verifies; the first frame starts at the offset the server answers; an
    answer below the sent offset makes the agent resend from it; bytes
    appended to the log while connected arrive in a later frame;
  - negative: no frame is sent while one is unanswered; a 4413 close
    returns full; a server that refuses the upgrade ends `ship` with an
    error; `read_at` past the end and on a missing file reads 0.
- `libs/runtime/tests.rs`: a console task for a deployment that turned
  STOPPING is aborted before the plan runs; `destroy_all` empties
  `consoles`.

## Acceptance

`cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D
warnings`, `cargo test -p jatszoter-agent` green; `make smoke` still passes
and `console.log` in the smoke VM's directory holds the boot.
