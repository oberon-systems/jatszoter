# 04 - Design: a board for every screen

Read `promts/00-README.md` first; its ground rules apply. Then read
`docs/project/08-web-ui.md` and the `Design system` page in
`dev/web/templates/base`.

## Goal

Every screen settled in `08-web-ui.md` has its boards in the `base`
template, built from the design system, approved and exported.

## Established facts

- A screen written as open in `08-web-ui.md` gets no board.
- The shell and every component come from page `Design system`; a screen
  that needs a new component adds it there first and says so.

## Scope

`dev/web/templates/base/` only, through the stack. No code.

Ask the user for `make design` with the `base` template. One Penpot page
per screen, named as the screen is named in `08-web-ui.md`; one board per
state:

- the filled state, with placeholder data only;
- the empty state of every list;
- every dialog and confirmation the screen opens;
- what a refusal and an API error look like on that screen;
- for the trainee page and the deployment detail: before READY, attached
  with control, attached without control, and after the link is revoked
  or the deployment ended;
- for the replay page: the player and the list of markers.

Hand the pages over in the order of `08-web-ui.md`, a few at a time, and
stop after each batch. After the last approval, ask for
`make -C dev/web down` with the export into `base`.

## Failure behaviour

A screen the user sends back is redrawn before the next batch. A screen
that cannot be drawn from `08-web-ui.md` as written is reported, and the
document is corrected first.

## Acceptance

- Every settled screen has its page and the user approved each one.
- The export in `dev/web/templates/base/` passes
  `.venv/bin/pre-commit run --files`.
- The report lists page and board names: later prompts address boards by
  them.
