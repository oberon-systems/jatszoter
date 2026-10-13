# 09 - Web: the replay page

Read `promts/00-README.md` first; its ground rules apply. Then read
`docs/project/05-recording.md`, `docs/project/08-web-ui.md` and the replay
page in `dev/web/templates/base`.

## Goal

An operator replays a recording in the browser and jumps to its markers.

## Established facts

- A recording is an asciicast v2 file with `o`, `r` and `m` events; the
  markers are control changes, settled checks and `recording limit
  reached`.
- `GET /api/v1/recordings/{id}.cast` answers a signed operator only, and
  serves a live recording as far as it is written.

## Scope

`packages/web` and its tests, with asciinema-player vendored under the
static directory.

- The replay page of one recording: the player, and the markers as a list
  of jump points with their time and text.
- The cast reaches the browser through a route of `web` that signs the
  API call; the file store URL never does.
- A recording that is still live says so, as its board shows.

## Failure behaviour

An unknown recording, or one whose file is gone, shows the board's 404
state. A link of any role gets nothing from this page.

## Acceptance

- `.venv/bin/pytest -q packages/web` passes: the page, the cast route,
  the markers of a sample cast, the refusals.
- By hand on a smoke stand: the recording of a session plays, and a jump
  to `check failed` lands on it.
- `make smoke` passes.
- `.venv/bin/pre-commit run --files <paths>` passes.
