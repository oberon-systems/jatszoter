# 05 - Agent: fetch the seed, pack the ISO, attach and eject it

Read `promts/00-README.md` first; its ground rules apply. Then read Agent
protocol and VM runtime in `docs/project/01-architecture.md` and Scenario
secrecy in `docs/project/06-security.md`.

## Goal

A start fetches the deployment's seed, packs it into `seed.iso` inside the
VM directory and boots the VM with it as a CD-ROM. A later call ejects it and
deletes the file. Nothing waits for the guest yet; prompt 06 does.

## Established facts

- `Api` is the agent's client trait; `HttpApi` implements it, and
  `libs/agent/tests.rs` has a `FakeApi` implementing it too. `Signed`,
  `Moved` and the 409-as-`None` convention of `deployments` are the model.
- `vm::create(root, spec, tools)` writes `vm.json`, creates the overlay,
  spawns QEMU and checks `query-status`; any failure destroys the
  directory. `VmSpec` and `Record` derive `Debug`; `Record` is `vm.json`.
- `vm::qemu_args(spec, dir, kvm)` is pure and tested; `option()` doubles
  commas in paths.
- `Tools { qemu, qemu_img }` comes from `Config`.
- The runtime's Start task (`libs/runtime/mod.rs`) runs assign, `ensure`,
  `vm::create`, and on error sends `fail` with a reason of at most 200
  characters.

## Scope

- `libs/api`:
  - `Seed { user_data, meta_data }`, `Deserialize` and `Clone`, with a
    hand-written `Debug` that prints no content;
  - `Api::seed(agent_id, credential, deployment_id) ->
    Result<Signed<Option<Seed>>>`: 200 is the seed, 409 is `None`, 401
    `Refused`. Give the trait method a default body answering
    `Ok(Signed::Accepted(None))` so `FakeApi` compiles unchanged;
    `HttpApi` implements it as a signed GET.
- `libs/config`: `JATSZOTER_AGENT_XORRISO` (default `xorriso`); `Tools`
  gains `xorriso`.
- `libs/vm`:
  - `VmSpec` gains `seed: Option<Seed>`; `Record` gains `created_at: u64`
    (unix seconds, `#[serde(default)]` so an older `vm.json` still reads);
  - `create` writes `vm.json` with `created_at`, then, when the spec has a
    seed, writes `user-data` and `meta-data` into a private `seed/`
    directory inside the VM directory, runs
    `xorriso -as mkisofs -quiet -o <dir>/seed.iso -V cidata -J -r <dir>/seed`
    with an argument vector, removes `seed/`, and makes `seed.iso` 0600 -
    all before the overlay; the existing cleanup covers every failure;
  - `qemu_args` adds the CD-ROM only when the spec has a seed:
    `-drive if=none,id=seed,media=cdrom,readonly=on,file=<dir>/seed.iso`
    and `-device ide-cd,id=seed0,drive=seed,bus=ide.1` on the q35 AHCI
    controller (check it starts under `-nodefaults`, adjust and say so if
    not);
  - `create` refuses a `disk_gib` below the base's `virtual-size` from the
    `qemu-img info` it already runs, with a clear error naming both sizes;
  - `eject_seed(dir)`: QMP `eject` with `id` `seed0` and `force: true`, then
    remove `seed.iso`; an already empty tray and a missing file are fine.
  - Update the test helpers in `libs/vm/tests.rs` for the two new fields;
    no other test edits.
- `libs/runtime`: Start fetches the seed after a successful assign and
  before `ensure`. `Refused` or `None` sends `fail` with reason
  `seed: not served`; an error sends `seed: <error>`; the seed goes into the
  spec. Prompt 06 adds the rest of the bootstrap.
- `main.rs`/runtime construction pass the new tool path.
- Tests: `libs/vm/tests.rs`, `libs/api/tests.rs`, and the ignored real-QEMU
  test extended.

## Failure behaviour

- A start whose seed cannot be fetched or packed leaves no VM directory and
  ends FAILED with a `seed: ...` reason.
- The seed's content never reaches a log, an error message or a reason;
  `xorriso`'s stderr may, cut like `qemu-img`'s.

## Tests

Positive: `qemu_args` with a seed has the CD-ROM pair, paths with commas
escaped; without a seed the argv is unchanged; `HttpApi::seed` decodes a
200; `Seed`'s `Debug` shows no content; `eject_seed` sends `eject` to a fake
QMP server and removes the file.

Negative: `HttpApi::seed` maps 409 to `None` and 401 to `Refused`; a
missing `xorriso` fails `create` with a clear error and no directory left;
`eject_seed` on a directory without `seed.iso` and with an empty tray is
fine; a `disk_gib` below the base's virtual size is refused before QEMU
starts. Ignored, needs QEMU and `xorriso`: create a VM from a blank disk with
a seed, see `seed.iso` attached (`query-block` lists `seed`), eject it,
`seed.iso` is gone, destroy it.

## Acceptance

`cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D
warnings`, `cargo test -p jatszoter-agent` green; with QEMU and `xorriso`,
`cargo test -p jatszoter-agent -- --ignored` green; `make smoke` still
passes (a blank disk never answers the guest agent, so it stays STARTING).
