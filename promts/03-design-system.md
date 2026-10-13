# 03 - Design system: the shared boards

Read `promts/00-README.md` first; its ground rules apply. Then read
`dev/web/README.md` and `docs/project/08-web-ui.md`.

## Goal

The `base` template holds a page `Design system` whose boards every screen
is later assembled from, approved by the user and exported.

## Established facts

- `dev/web/templates/` holds no template yet; `base` starts from `(empty)`.
- The Penpot MCP server works on the file open in the browser tab.
- The terminal is xterm.js and the replay is asciinema-player: both draw
  their own text area, the boards draw only the frame around it.

## Scope

`dev/web/templates/base/` only, through the stack. No code.

Ask the user for `make -C dev/web up`, then draw on page `Design system`:

- colors, as named tokens, with the contrast of each text and surface
  pair;
- typography: the type scale, and one monospace face for the terminal;
- the layout shell: top bar, navigation over the operator screens of
  `08-web-ui.md`, page header with title and primary action;
- tables: header, row, empty state, pagination or its absence;
- forms: text field, select, checkbox, error text, primary and dangerous
  buttons, the confirmation dialog;
- status badges for every deployment status, agent state and check status
  the docs name;
- the terminal frame: who holds control, the take and give back action,
  the check button and its last result;
- flash messages: success, refusal, error.

Hand the boards over and stop. After the user approves them, ask for
`make -C dev/web down` with the export into `base`.

## Failure behaviour

Nothing is exported before the approval. A change the user asks for is
drawn and handed over again.

## Acceptance

- The user approved the boards in so many words.
- `dev/web/templates/base/` holds the export, and
  `.venv/bin/pre-commit run --files` over it passes.
- No raster image is in the export.
