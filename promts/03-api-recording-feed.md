# 03 - API: the routes feed the recording

Read `promts/00-README.md` first; its ground rules apply. Then read
Writing in `docs/project/05-recording.md`, Pipeline, Links, Take control
and Resize in `docs/project/04-console.md`, and `models/recording.py` as
prompt 02 left it.

## Goal

A trainee link that is opened leaves a recording of what its terminal
showed: output, resizes and control changes, closed when the link is
revoked or the deployment ends.

## Established facts

- `open_link` in `routes/links.py` loads the link and its user and calls
  `link.open()`, which stamps `opened_at` on the first open.
- `ship` in `routes/console.py` calls `console.append` in a thread and
  publishes the bytes it kept, with the offset they start at.
- `_forward` passes a holder's resize to `hub.to_agent`, which answers
  whether the agent took it; `control` stores and announces the holder.
- `revoke` in `routes/links.py` revokes one link. `Deployment.transition`
  stamps `revoked_at` on every link itself once the target is terminal,
  and takes `filer_url`; `Deployment.stop` and `Agent.heartbeat` call it
  without one.
- `Link.recording` loads the link's Recording row; a miss is `IndexError`.
- Two tabs may open a fresh link at the same moment; `recordings.link_id`
  is unique.

## Scope

- `routes/links.py`, `open_link`: on the first open of a link whose user is
  a trainee, build the Recording and start it. The title is the scenario's
  slug, its version and the deployment id. Losing the race for the row is
  not an error: the open goes on.
- `routes/console.py`:
  - `ship`: after a frame is stored, in the same thread, the deployment's
    live recording takes the kept bytes; without one nothing more is done;
  - `_forward`: a resize the agent took is written as `r`;
  - `control`: a change of the holder is written as `m`, `control
    operator` or `control trainee`.
- Closing: `revoke` closes the link's recording. A terminal transition
  closes the recording of every link it revokes, so `Deployment.stop`,
  `Agent.heartbeat` and their routes pass `filer_url` down.
- `models/deployment.py`, `models/agent.py`, `routes/deployments.py`,
  `routes/agents.py`: only what passing `filer_url` needs.

## Failure behaviour

- A recording that cannot be written never stops the console: `ship` still
  answers its offset and publishes, the control route still answers, the
  resize still reaches the agent. The failure is logged with the
  deployment id and the error's type, nothing else.
- A link that cannot start its recording still opens.
- Keys never reach the recording, not from `_forward` and not from any
  other path; a viewer link's resize and a resize from the party without
  control write no `r`.

## Tests

- `tests/test_links_api.py`: the first open of a trainee link stores one
  Recording with a header in its file; a second open adds none; an
  operator or viewer link stores none; revoking the link stores a size and
  a duration.
- `tests/test_console_api.py`, with a fake agent socket:
  - positive: bytes shipped after READY and after the open appear as `o`;
    the holder's resize appears as `r`; taking control and giving it back
    appear as two `m` events, in order with the output;
  - negative: bytes shipped before READY are absent; keys typed by the
    trainee and by the operator appear nowhere in the file; a resize the
    agent did not take writes nothing; with the file store failing, the
    agent socket still gets its offset.
- `tests/test_deployments.py` or `tests/test_agents.py`: a deployment that
  completes, and one aborted on a lapsed lease, close the recording.

## Acceptance

`.venv/bin/pytest -q packages/api` green; `make smoke` passes;
`.venv/bin/pre-commit run --files` on every edited file passes.
