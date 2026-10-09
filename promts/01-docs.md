# 01 - Docs: resize proven, then the item 8 contracts

Read `promts/00-README.md` first; its ground rules apply. Then read
`docs/project/04-console.md` in full, Operator API, Agent protocol and VM
runtime in `docs/project/01-architecture.md`, Entities in
`docs/project/02-domain-model.md`, Credentials, Link threats, Guest and host
and Web in `docs/project/06-security.md`, `docs/project/07-roadmap.md` and
`make/tests/smoke.sh`.

## Goal

A Rocky 9 guest is shown to accept a terminal size through the guest agent,
and `docs/` then states every decision in the README of this prompt set, so
prompts 02 to 06 implement a written contract. No code changes land.

## Established facts

- `04-console.md` already has Viewers, Links, Take control and Resize; they
  predate the decisions and leave open how a link becomes a session, what a
  trainee sees, how keys reach the agent and which close codes apply.
- `06-security.md` promises a CSRF token on every state-changing request
  and an `Origin` check on upgrades; the README narrows that for item 8.
- The bootstrap empties `FILTER_RPC_ARGS`, so `guest-exec` is allowed, but
  its children run confined as `virt_qemu_ga_t`: `sudo` and `setsebool` are
  denied there. Whether `stty` may set the size of `/dev/ttyS0` from that
  domain has never been tried.
- `smoke.sh` leaves `qga.sock` in `$TEMP_DIR/agent/vms/$deployment/` while
  the deployment is READY; the agent connects it only before READY.
- The guest agent protocol is one JSON object per line: `guest-exec` takes
  `path`, `arg` and `capture-output` and returns a `pid`;
  `guest-exec-status` takes that `pid` and returns `exited`, `exitcode`
  and base64 `out-data`.

## Scope

- The proof, tried locally and reverted, never committed: a temporary step
  in `smoke.sh` after the READY checks, a Python one-shot over `qga.sock`,
  that
  - runs `stty -F /dev/ttyS0 cols 100 rows 30` and reads its exit code;
  - runs `stty -F /dev/ttyS0 size` with `capture-output` and reads
    `30 100`;
  - prints the two exit codes and whether the size matched, nothing else.
- If either exit code is not 0 or the size does not match: stop, report
  the exit codes and the guest's audit denial if one is readable, and
  write nothing. The user chooses another resize path.
- `docs/project/04-console.md`:
  - Pipeline: what travels down the agent socket - keys as binary frames,
    a resize as `{"cols": n, "rows": n}` - next to the `{"offset": n}`
    answers; the 256 message bound.
  - Viewers: signed operator or link cookie; READY only for a link; where
    a trainee or viewer link starts; frame limits; close code 4403.
  - Links: the users behind them, the trainee link on create, one live
    trainee link, issue and revoke routes, `GET /s/{token}`, the cookie and
    its session in Valkey, the 303 to `/t/{deployment_id}`.
  - Take control: the holder in Valkey, its default, who may set it and
    when, which socket role types.
  - Resize: the command, the 5 s bound, only on a change, and that the
    proof above stands behind it.
  - Acceptance criteria: a viewer link never types; a revoked link's
    socket closes with 4403; the token appears in no log.
- `docs/project/01-architecture.md`: the Operator API list gains the users,
  links, control and `/s/{token}` routes with who may call each; a line on
  cookie authentication beside the one on signed sockets.
- `docs/project/02-domain-model.md`: User says who creates one and that a
  user without a password cannot log in; Deployment and Link say the
  trainee link is made on create; Ephemeral state gains the link session
  and the control holder.
- `docs/project/06-security.md`: the Link bullet and the Web section state
  the item 8 rule on `Origin` and that CSRF tokens arrive with item 11; the
  threat table's cookie row names the path scope.
- `docs/project/07-roadmap.md`: item 8 lists what this set delivers (API,
  agent, smoke), in the style of item 7.

## Failure behaviour

Documentation only. The proof leaves no change in the tree and no QEMU
behind: `cleanup` in `smoke.sh` still runs.

## Tests

None. The documents pass markdownlint through pre-commit, and
`grep -nP '[^\x00-\x7F]' docs/project/*.md` prints nothing.

## Acceptance

- The proof's result is reported before any document changes.
- Every decision in `promts/00-README.md` is findable in `docs/`, worded as
  a contract rather than a plan.
- No document contradicts another: the cookie, the close codes, the holder
  and the routes read the same in 01, 02, 04 and 06.
- `.venv/bin/pre-commit run --files` on every edited document passes, and
  `git status` shows no change to `make/tests/smoke.sh`.
