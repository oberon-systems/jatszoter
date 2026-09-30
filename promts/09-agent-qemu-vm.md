# 09 - Agent: one QEMU VM - create, inspect, destroy

Read `promts/00-README.md` first; its ground rules apply. Then read VM runtime
and Reconciliation in `docs/project/01-architecture.md` and Guest and host in
`docs/project/06-security.md`.

## Goal

A module that creates one VM from a base image, finds VMs already on disk,
talks QMP, and destroys a VM completely. It knows nothing about the API or
deployment states; prompt 10 drives it.

## Layout

`<state_dir>/vms/<deployment_id>/`, 0700:
`vm.json` (`deployment_id`, `vm_id`, 0600), `disk.qcow2`, `qemu.pid`,
`qmp.sock`, `serial.sock`, `qga.sock`.

## Scope

- `Cargo.toml`: tokio features `process`, `net`, `io-util`, `fs`, `time`;
  `nix` with `signal` and `process` for `kill` (no `unsafe`).
- `libs/config`: `JATSZOTER_AGENT_QEMU` (default `qemu-system-x86_64`) and
  `JATSZOTER_AGENT_QEMU_IMG` (default `qemu-img`).
- `libs/qmp/mod.rs`: a minimal QMP client over `tokio::net::UnixStream`:
  read the greeting, `qmp_capabilities`, then `execute(command, args)`
  returning `return` or the `error.desc`; skip `event` messages; every read
  under a timeout.
- `libs/vm/mod.rs`:
  - `VmSpec { deployment_id, vm_id, cpus, memory_mib, disk_gib, base }`;
  - `fn qemu_args(spec, dir, kvm: bool) -> Vec<OsString>`, pure and tested:
    `-name`, `-nodefaults`, `-no-user-config`, `-display none`,
    `-machine q35,accel=kvm` with `-cpu host` or `accel=tcg` with `-cpu max`,
    `-smp`, `-m`, the overlay on virtio, user-mode network
    `-netdev user,id=net0 -device virtio-net-pci,netdev=net0`,
    the serial console on `serial.sock`, the guest agent channel
    `org.qemu.guest_agent.0` on `qga.sock` through `virtio-serial`, QMP on
    `qmp.sock`, all sockets `server=on,wait=off`, `-daemonize`,
    `-pidfile qemu.pid`. No port forwarding, no host shell;
  - KVM when `/dev/kvm` opens read-write for the agent's user;
  - `async fn create(root, spec, tools) -> Result<Vm>`: refuse an existing
    directory; refuse socket paths over 107 bytes with a clear error; write
    `vm.json`; `qemu-img info --output=json` on the base for its format;
    `qemu-img create -f qcow2 -F <format> -b <base> disk.qcow2 <disk_gib>G`;
    spawn QEMU and wait for it to exit (it daemonizes) with success; wait for
    `qmp.sock`; QMP `query-status` must say `running`. Any failure after the
    directory exists destroys what was made before returning the error;
  - `fn list(root) -> Vec<OnDisk>`: every directory with a readable
    `vm.json`, with `alive` = the pid in `qemu.pid` exists and its
    `/proc/<pid>/cmdline` contains the `vm_id` (a reused pid is not ours);
    directories without `vm.json` are listed as orphans to destroy;
  - `async fn destroy(dir)`: QMP `quit` (5 s), wait for the pid to go (10 s),
    else `SIGKILL`; remove the directory. Idempotent: missing pid, dead
    process or missing directory are fine.
- Tests in `libs/qmp/tests.rs` against a fake QMP server on a temp socket,
  and in `libs/vm/tests.rs` for `qemu_args`, `list` and `destroy` on fake
  directories. Tests that need real QEMU are `#[ignore]` with a comment
  saying so: create from a blank `qemu-img create -f qcow2 base.qcow2 64M`,
  see it running and listed, destroy it, nothing left.

## Failure behaviour

A VM that failed to start leaves no directory and no process. Errors carry
the QEMU or `qemu-img` stderr, cut to a few hundred bytes.

## Tests

Positive: argv for KVM and TCG; QMP handshake, command, error reply, event
skipped; `list` sees a live fake (a sleeping child whose cmdline carries the
vm id, set through `CommandExt::arg0` on `sleep`) and a dead one; `destroy` removes both.

Negative: existing directory refused; overlong socket path refused before
anything is written; a QMP server that never greets times out; a pid file
naming an unrelated process is reported not alive and never killed.

## Acceptance

`cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D
warnings`, `cargo test -p jatszoter-agent` green; with QEMU installed,
`cargo test -p jatszoter-agent -- --ignored` green too.
