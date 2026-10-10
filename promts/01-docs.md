# 01 - Docs: the item 9 contracts

Read `promts/00-README.md` first; its ground rules apply. Then read
`docs/project/05-recording.md` and `docs/project/04-console.md` in full,
Operator API and the Stop flow in `docs/project/01-architecture.md`,
Entities and Ephemeral state in `docs/project/02-domain-model.md`, and
`docs/project/07-roadmap.md`.

## Goal

`docs/` states every decision in the README of this prompt set, so prompts
02 to 05 implement a written contract. No code changes land.

## Established facts

- `05-recording.md` predates the decisions. It puts the file at the store
  root, starts times at the first open without saying which bytes come
  first, and lists check markers, the player and the retention sweep as if
  item 9 delivered them.
- `04-console.md` says a trainee's view starts at the READY offset and that
  the link opens in any non-terminal status, so a link can be opened before
  there is anything a trainee may see.
- The Recording row today has `link_id`, `url`, `size_bytes` and
  `duration_seconds`; nothing writes one yet.
- Console bytes are raw; an asciicast event is a JSON string.

## Scope

- `docs/project/05-recording.md`:
  - Format: the header with `version`, `width` 80, `height` 24, `timestamp`
    and `title`; `o` as decoded text with the split and invalid byte rules;
    `r` as `<cols>x<rows>`; `m` for control changes now, and for check
    results once item 10 lands; no `i`.
  - Writing: the row and the header on the first open of a trainee link;
    time zero; output from the READY offset, with the first event of a link
    opened after READY; one filer append per event, in time order; where an
    `r` and an `m` come from; closing on revoke and on the deployment's
    end; what a lost Valkey costs.
  - Storage: the path `recordings/<deployment_id>/<recording_id>.cast`; the
    row's fields; the limit and its marker, after which nothing is written;
    the retention sweep named as item 12.
  - Replay: the listing and the download route, signed operator only, a
    live recording served as far as written; the player named as item 11.
  - Acceptance criteria: reworded where they promised check markers.
- `docs/project/04-console.md`: Pipeline, Links, Take control and Resize
  agree with 05 on when a recording starts, what goes into it and when the
  `r` event is written.
- `docs/project/01-architecture.md`: the Operator API list gains the two
  recording routes; the Stop flow says which transition closes the
  recording.
- `docs/project/02-domain-model.md`: Recording gains its deployment;
  Ephemeral state gains the `recording:*` keys.
- `docs/project/07-roadmap.md`: item 9 lists what this set delivers, in the
  style of item 8; item 10 gains check results as markers, item 11 the
  player with markers as jump points, item 12 the recording retention
  sweep.

## Failure behaviour

Documentation only.

## Tests

None. The documents pass markdownlint through pre-commit, and
`grep -nP '[^\x00-\x7F]' docs/project/*.md` prints nothing.

## Acceptance

- Every decision in `promts/00-README.md` is findable in `docs/`, worded as
  a contract rather than a plan.
- No document contradicts another: the path, the routes, the events and
  the moment a recording starts and closes read the same in 01, 02, 04, 05
  and 07.
- `.venv/bin/pre-commit run --files` on every edited document passes.
