# 01 - Docs: the item 7 contracts

Read `promts/00-README.md` first; its ground rules apply. Then read
`docs/project/04-console.md` in full, Agent protocol and VM runtime in
`docs/project/01-architecture.md`, Writing in `docs/project/05-recording.md`
and `docs/project/07-roadmap.md`.

## Goal

`docs/` states every decision in the README of this prompt set, so prompts
02 to 06 implement a written contract. No code changes.

## Established facts

- `04-console.md` already describes the pipeline, viewers, links, take
  control, resize, backpressure and scaling; several lines predate these
  decisions:
  - "one WebSocket per READY deployment" - it is per live VM now;
  - the console buffer "up to `JATSZOTER_CONSOLE_LIMIT_BYTES`" - it is now
    a Valkey tail plus a full log in the file store, two settings;
  - "Bytes produced before READY belong to the operator's bootstrap log
    only" stays true and now has a mechanism: the READY offset.
- `01-architecture.md` lists the agent routes, among them
  `WS .../console`, and the VM directory layout without `console.log`.
- `05-recording.md` streams the trainee's recording from the console bytes
  the API receives; item 9 implements it and nothing here changes that.

## Scope

- `docs/project/04-console.md`:
  - Pipeline: QEMU writes `console.log` itself; one socket per live VM in
    STARTING, BOOTSTRAPPING or READY; the frame and reply format; the agent
    moves its offset to the API's answer, backwards too; poll 50 ms,
    reconnect 5 s, no HTTP fallback; close code 4413 when the log is full
    and the agent stops shipping that deployment.
  - A new section on what the API holds: the Valkey tail
    (`JATSZOTER_CONSOLE_TAIL_BYTES`, 8 MiB), the full log
    `consoles/<deployment_id>.log` in the file store
    (`JATSZOTER_CONSOLE_LIMIT_BYTES`, 64 MiB), the READY offset taken when
    `bootstrap` is accepted, the download route, and what a terminal status
    drops.
  - Viewers: a new viewer gets the tail, older history stays in the browser;
    in version one item 7 serves operators only, links follow in item 8.
  - Backpressure: the per-viewer cap and the resend from the Valkey tail.
  - Acceptance criteria: replace "an agent reconnect loses and duplicates no
    console byte" with the same claim for the full log, and add that an API
    restart with Valkey alive keeps the tail.
- `docs/project/01-architecture.md`: `console.log` in the VM directory
  listing; a VM runtime bullet on the serial log file; the agent protocol
  list gains `GET /api/v1/deployments/{id}/console` where the operator
  routes are listed, or a pointer to 04 if they are not listed there.
- `docs/project/07-roadmap.md`: item 7 lists what this set delivers (API,
  agent, smoke), in the style of item 6.

## Failure behaviour

None: documentation only.

## Tests

None. The documents pass markdownlint through pre-commit, and
`grep -nP '[^\x00-\x7F]' docs/project/*.md` prints nothing.

## Acceptance

- Every decision in `promts/00-README.md` is findable in `docs/`, worded as
  a contract rather than a plan.
- No document contradicts another: the agent socket lifetime, the two
  settings and the close code read the same in 01 and 04.
- `.venv/bin/pre-commit run --files` on every edited document passes.
