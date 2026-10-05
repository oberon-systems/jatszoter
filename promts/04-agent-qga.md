# 04 - Agent: guest agent client

Read `promts/00-README.md` first; its ground rules apply. Then read VM
runtime in `docs/project/01-architecture.md` and Bootstrap in
`docs/project/03-scenario-format.md`.

## Goal

A minimal client for qemu-guest-agent over `qga.sock`: say whether the guest
agent answers, and read one small file from the guest. Prompt 06 drives it;
item 10 extends it with `guest-exec` and file writes.

## Established facts

- `vm::qemu_args` puts the guest agent channel `org.qemu.guest_agent.0` on
  `qga.sock` through `virtio-serial`, `server=on,wait=off`.
- QEMU accepts the socket whether or not a guest agent runs inside: before
  the guest boots, writes vanish and reads time out.
- Unlike QMP there is no greeting and no capabilities step. Replies to a
  request an earlier client gave up on may still sit in the channel, which
  is why the protocol has `guest-sync-delimited`: the client sends `0xFF`
  then `{"execute":"guest-sync-delimited","arguments":{"id":N}}`, and the
  guest answers `0xFF` followed by `{"return":N}`; everything before the
  matching reply is stale and dropped.
- `libs/qmp/mod.rs` is the model for framing, timeouts and errors.
- `AgentError` is in `libs/error/mod.rs`.

## Scope

- `Cargo.toml`: `base64` for `buf-b64`.
- `libs/error`: a `Qga(String)` variant.
- `libs/qga/mod.rs`, `Qga` over `tokio::net::UnixStream`:
  - `connect(path, timeout)`: connect, then sync with a fresh id (any value
    that differs between calls, from the clock is fine), skipping bytes up
    to `0xFF` and lines whose `return` is not that id; every read under the
    timeout;
  - `execute(command, args) -> Result<Value>`: one JSON line out, `return`
    back or the `error.desc` as `AgentError::Qga`;
  - `ping()`: `guest-ping`;
  - `read_file(path, max) -> Result<Option<Vec<u8>>>`: `guest-file-open`
    with mode `r`, `guest-file-read` in chunks until `eof` or more than
    `max` bytes (an error), `guest-file-close` always, even after a failed
    read; a file that does not exist is `Ok(None)` (the guest answers with
    an `error.desc` naming `No such file or directory`), any other open
    error is an error.
  - Lines are capped like QMP's; a reply that is not JSON is an error.
- `libs/mod.rs`: register the module; `dead_code` allows naming prompt 06
  where nothing calls it yet.
- Tests in `libs/qga/tests.rs` against a fake guest agent on a temp socket.

## Failure behaviour

A guest agent that does not answer is a timeout error, never a hang; callers
treat it as "not yet". A read never leaves a guest file handle open on
purpose.

## Tests

Positive: sync drops a stale reply and a stray `0xFF`; `ping`; a file read
in two chunks; a missing file is `None`.

Negative: no answer times out; an `error` reply carries its `desc`; a file
over `max` is an error and the handle is still closed; a non-JSON reply is
an error.

## Acceptance

`cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D
warnings`, `cargo test -p jatszoter-agent` green; `make smoke` still passes.
