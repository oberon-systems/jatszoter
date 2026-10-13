# 07 - Web: the operator lists and forms

Read `promts/00-README.md` first; its ground rules apply. Then read
`docs/project/08-web-ui.md` and, in `dev/web/templates/base`, the pages of
the screens named below.

## Goal

The operator screens that are lists and forms render from the shell and
do what their boards show.

## Established facts

- The shell, the session and CSRF come from prompt 06; the API calls come
  from prompt 05.
- The deployment detail, the trainee page and the replay page are prompts
  08 and 09.

## Scope

`packages/web` and its tests.

Build, one screen at a time and in the order of `08-web-ui.md`:
deployments list and create; scenarios and images; agents; users; logs;
storage management; results. A screen written as open, or without an
approved board, is skipped and named in the report.

- A list is one template and one partial HTMX swaps; no client state.
- A create or a change posts a form, shows the API's refusal next to the
  field it concerns, and redirects on success.
- A dangerous action asks for the confirmation its board shows.
- The trainee link token is shown once, on the answer that creates the
  deployment or the link, as its board shows, and never again.

## Failure behaviour

An empty list shows its empty board. An API refusal shows the flash
message of the design system; nothing is retried silently.

## Acceptance

- `.venv/bin/pytest -q packages/web` passes, with a rendering test and a
  refusal test for every screen built.
- No page ships markup its board does not show.
- `make smoke` passes.
- `.venv/bin/pre-commit run --files <paths>` passes.
