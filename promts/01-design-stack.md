# 01 - Design stack: the Penpot stack is current and its exports lint

Read `promts/00-README.md` first; its ground rules apply. Then read
`dev/web/README.md`, `dev/web/Makefile`, `dev/web/.penpot.yaml`, the root
`Makefile` and `.pre-commit-config.yaml`.

## Goal

The disposable Penpot stack in `dev/web` is ready for the design prompts:
on the latest `penpot-local-stack`, with an export that passes the hooks
and packed archives kept out of git.

## Established facts

- The stack is already wired: `dev/web/Makefile` passes `up`, `down`,
  `import`, `export`, `extract` and `convert` to
  `../../.venv/bin/penpot-stack`; the root `Makefile` has no design
  target, the stack lives in `dev/web` alone; `dev/web/.penpot.yaml`
  names the Compose project, the port 9002 and the Penpot version; `dev/web/README.md` describes the loop.
- `requirements.txt` pins `penpot-local-stack==0.1.1`; the latest release
  is 0.1.3: 0.1.2 made the image tags literal and moved to Postgres 18,
  whose tmpfs mount only 0.1.3 fixes.
- `dev/web/templates/` holds only `.gitkeep`: no template was exported yet.
- An export is a directory with `manifest.json`, `files/`, `objects/` and
  `state.json`; `state.json` may carry non-ASCII text, which the
  `require-ascii` hook refuses.
- `convert` writes `<name>.penpot` into `dev/web/`.
- `.mcp.json` is ignored by git; the README says how to add the `penpot`
  server.

## Scope

`requirements.txt`, `.pre-commit-config.yaml`, `.gitignore`, `Makefile`,
`dev/web/README.md`. Nothing under `packages/`.

- Pin `penpot-local-stack==0.1.3`.
- Ignore `dev/web/*.penpot`, with a one-line comment saying the unpacked
  template is what git tracks.
- Let an exported template pass the hooks: exclude from a hook only what
  a real export trips, for the export's paths alone, each with a reason in
  a comment. `state.json` under `require-ascii` is the likely one. Find
  them by running the hooks over a real export, see Acceptance; guess
  nothing.
- Keep `dev/web/README.md` true: the version the stack is pinned to is
  named nowhere but `requirements.txt` and `.penpot.yaml`, and the Commands
  table matches the Makefile.

## Failure behaviour

A hook that fails on an export is excluded for the export's paths alone,
never switched off. An export holding a raster image stays refused.

## Acceptance

- `make install` installs 0.1.3 and `.venv/bin/penpot-stack` starts.
- `make -C dev/web up` brings the stack up with `(empty)`; a file named `base`
  with one empty page is exported by `make -C dev/web down` into
  `dev/web/templates/base/`.
- `.venv/bin/pre-commit run --files` over that export and the edited files
  passes.
- `make -C dev/web convert` writes `base.penpot`, and `git status` does not
  list it.
- The trial export is removed again: prompt 03 creates the real `base`.
