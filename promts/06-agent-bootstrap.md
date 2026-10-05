# 06 - Agent: drive a VM from STARTING to READY

Read `promts/00-README.md` first; its ground rules apply. Then read Data
flows and Reconciliation in `docs/project/01-architecture.md`, Bootstrap in
`docs/project/03-scenario-format.md` and Who moves what in
`docs/project/02-domain-model.md`.

## Goal

Every reconcile round probes each live VM still bootstrapping: the guest
agent answering moves it to BOOTSTRAPPING, the marker moves it to READY or
FAILED, and the deadline fails it. Each probe is a short task, so a stop is
never held up by a guest.

## Established facts

- `reconcile::plan` adopts a live VM whose deployment is STARTING,
  BOOTSTRAPPING or READY with no action; `Lost` covers a dead one.
- `QemuRuntime` keeps one task per deployment id; a busy deployment gets no
  action, so today a STOPPING deployment waits for its start task to end.
- Prompt 04 gave `Qga::connect`, `ping`, `read_file`; prompt 05
  `vm::eject_seed`, `Record.created_at` and the seed in the start.
- Events: `boot` is STARTING to BOOTSTRAPPING, `bootstrap` is BOOTSTRAPPING
  to READY, `fail` takes the current status as `expected`.

## Scope

- `libs/bootstrap/mod.rs`, pure and tested without I/O:
  - `MARKER: &str = "/var/lib/jatszoter/marker"`;
  - `verdict(marker: &[u8]) -> Verdict` with `Ready` for `ok`,
    `Failed(reason)` for `failed <step> <exit code>` (the marker text, cut to
    200 characters, printable ASCII only), and `Failed("bad marker")` for
    anything else; surrounding whitespace ignored;
  - `overdue(created_at, bootstrap_seconds, now) -> bool`.
- `libs/reconcile`: `Action::Probe(Desired)` for a live VM whose deployment
  is STARTING or BOOTSTRAPPING and not busy; READY stays adopted with no
  action. `Action::deployment_id` covers it.
- `libs/runtime`:
  - the task map remembers each task's kind; before planning, a Start or
    Probe task whose deployment is now STOPPING is aborted and dropped from
    the busy set, so the plan's Stop destroys what it left;
  - the Probe task, bounded as a whole by a short timeout (15 s):
    1. `created_at` from the VM's `vm.json`; past
       `timeouts.bootstrap_seconds` sends `fail` with reason
       `bootstrap timeout` and ends;
    2. STARTING: connect to `qga.sock` with a short timeout and `ping`;
       success sends `boot`; no answer does nothing;
    3. BOOTSTRAPPING: `read_file(MARKER, 256)`; no file does nothing;
       `Ready` runs `eject_seed`, then sends `bootstrap` (an eject error
       logs and retries next round, no transition); `Failed(reason)` sends
       `fail` with that reason;
  - Start ends after `vm::create` as before; the next round probes it.
- Tests: `libs/bootstrap/tests.rs`, `libs/reconcile/tests.rs` (new cases
  only), `libs/runtime/tests.rs` for the abort.

## Failure behaviour

- An agent restart adopts a VM mid-bootstrap and probes it again; the
  deadline still counts from `created_at`.
- A failed or overdue bootstrap ends FAILED; the VM goes on the next round
  through `Remove`, as for any deployment no longer desired.
- A marker never reaches a shell, a log line beyond its reason, or a
  transition longer than 200 characters.

## Tests

Positive, pure: `ok` is `Ready`; `failed configure 3` is `Failed` with that
text; `overdue` at and after the deadline. In `plan`: a live STARTING VM
and a live BOOTSTRAPPING VM get `Probe`; a live READY VM gets nothing.

Negative: an empty or garbled marker is `Failed("bad marker")`; a marker
with control characters is cleaned; a busy STARTING VM gets no `Probe`; a
dead STARTING VM is still `Lost`. In the runtime: a pending Start task for a
deployment that turned STOPPING is aborted before the plan runs.

## Acceptance

`cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D
warnings`, `cargo test -p jatszoter-agent` green; `make smoke` still passes
(the blank disk stays STARTING until its deadline, long after the smoke
stops it).
