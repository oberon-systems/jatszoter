# 10 - Agent: the runtime driven by desired state

Read `promts/00-README.md` first; its ground rules apply. Then read Agent
protocol, Data flows and Reconciliation in `docs/project/01-architecture.md`
and Who moves what in `docs/project/02-domain-model.md`.

## Goal

After each heartbeat the agent reads its desired state and makes the VMs on
the host match it: start what was assigned, destroy what is stopping or
unknown, fail what was lost. Long work (downloads, boots) never delays a
heartbeat.

## Established facts

- `libs/agent/mod.rs`: `Agent::cycle` heartbeats and returns the pause;
  `fence_if_lapsed` calls `Runtime::destroy_all`; `NoRuntime` is what
  `main.rs` uses today; `tests.rs` has `FakeApi` and `CountingRuntime`.
- Prompt 07 gave `Api::deployments` and `Api::transition`; prompt 08
  `ImageCache::ensure`; prompt 09 `vm::create`, `vm::list`, `vm::destroy`.
- Until item 6 nothing moves STARTING to BOOTSTRAPPING; a started VM stays
  STARTING, and that is correct here.

## Scope

- `libs/reconcile/mod.rs`, pure and tested without I/O:
  `fn plan(desired: &[Desired], on_disk: &[OnDisk], busy: &HashSet<id>,
  capacity: u32) -> Vec<Action>` with
  - `Start(Desired)`: PENDING assigned to us, no VM, not busy, within
    capacity counting running VMs and starts in flight;
  - `Stop(Desired)`: STOPPING, not busy - destroy the VM if any, then
    `destroy`;
  - `Lost(Desired)`: STARTING, BOOTSTRAPPING or READY, no live VM, not busy -
    `fail` with reason `vm lost`;
  - `Remove(dir)`: a VM on disk (alive or not) whose deployment is not in the
    desired state, or a dead VM whose deployment is desired only as terminal
    or PENDING, or an orphan directory;
  - a live VM whose deployment is STARTING, BOOTSTRAPPING or READY is
    adopted: no action.
- `QemuRuntime` (in `libs/agent` or its own module) implementing `Runtime`:
  - owns the `ImageCache`, the VM root and a map deployment id -> running
    task (`tokio::task::JoinHandle`) so a deployment is never worked on
    twice;
  - `reconcile(desired)` takes the plan and spawns each action as a task:
    - Start: `transition(assign, PENDING)`; `Conflict` drops it quietly;
      `ensure` the image, `vm::create`; any error sends `fail` with a short
      reason (`image: ...`, `vm: ...`, at most 200 characters, no URL query);
    - Stop: `vm::destroy`, then `transition(destroy, STOPPING)`;
    - Lost: `transition(fail, <status>, "vm lost")`;
    - Remove: `vm::destroy`;
  - tasks log with `deployment_id` and `vm_id`; finished tasks are reaped
    at the next reconcile;
  - `destroy_all` (fencing) aborts every task, then destroys every VM on
    disk. Make `Runtime` async if needed and adjust `fence_if_lapsed`.
- `Agent::cycle`: after an accepted heartbeat, read `deployments`;
  `Refused` goes back to registration like a refused heartbeat, `None` (no
  lease) skips the round; otherwise hand the list to `runtime.reconcile`,
  which returns at once. A failed read logs a warning and does not change
  the pause.
- `main.rs`: build `QemuRuntime` from the config
  (`<state_dir>/images`, `<state_dir>/vms`, the QEMU tools) and use it;
  remove `NoRuntime` if nothing uses it any more; update the `Runtime` doc
  comment that mentions roadmap item 4.
- Tests: `libs/reconcile/tests.rs` for `plan`; `libs/agent/tests.rs` for the
  cycle with `FakeApi` and a fake runtime that records `reconcile` calls.

## Failure behaviour

- An agent restart finds its daemonized VMs through `vm::list` and adopts
  those still desired; it never reboots a lost one.
- A deployment whose start failed ends FAILED with its VM directory gone.
- A lapsed lease destroys everything, in-flight starts included.

## Tests

Positive, in `plan`: assigned PENDING starts; STOPPING stops; live VM with
READY adopted; capacity 1 with one running VM starts nothing new.

Negative, in `plan`: busy deployments get no action; a VM for an unknown
deployment is removed; a STARTING deployment with a dead VM is `Lost`; an
orphan directory is removed. In the cycle: `None` desired state reconciles
nothing; `Refused` forgets the credential; fencing aborts in-flight tasks.

## Acceptance

`cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D
warnings`, `cargo test -p jatszoter-agent` green; `make smoke` still passes
(no deployment is created there yet, so the agent must idle cleanly).
