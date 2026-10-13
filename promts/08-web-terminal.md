# 08 - Web: the deployment detail and the trainee page

Read `promts/00-README.md` first; its ground rules apply. Then read
`docs/project/04-console.md`, `docs/project/08-web-ui.md` and the pages of
the deployment detail and the trainee page in `dev/web/templates/base`.

## Goal

An operator works a deployment from its detail page, and a trainee who
opened a link works in the terminal at `/t/{deployment_id}` and asks for
a check.

## Established facts

- The attach socket sends text frames with `offset` and `control` and
  binary frames with console bytes; it takes keys as binary frames and a
  resize as `{"cols": n, "rows": m}`.
- The API forwards keys only from a socket of the holder's role.
- A revoked link closes its sockets with 4403; a slow viewer is closed
  with 4429 and the browser reconnects.
- The check routes answer `detail` to an operator only.

## Scope

`packages/web` and its tests, with xterm.js vendored under the static
directory.

- The trainee page: the terminal, who holds control, the check button,
  the last result with its message, and every state its boards show.
- The deployment detail: status, the terminal as the operator, take
  control and give it back, links with create and revoke, checks with
  their detail, recordings with a link to the replay page, stop.
- One terminal script for both pages, loaded as a file: it attaches,
  resizes, reconnects after 4429 and stops for good after 4403.
- A check is asked for once: the button is off while one is pending, and
  a 409 shows as the board says.

## Failure behaviour

A socket that cannot attach shows the board's state for it and retries
with a growing pause. The trainee page never shows a check's detail and
never offers control.

## Acceptance

- `.venv/bin/pytest -q packages/web` passes: both pages in every state,
  the trainee page without `detail`, the refusals.
- By hand on a smoke stand: a trainee types, the operator takes control,
  the trainee's keys stop, a check is asked for and its result shows.
- `make smoke` passes.
- `.venv/bin/pre-commit run --files <paths>` passes.
