# 01 - Docs: the item 10 contracts

Read `promts/00-README.md` first; its ground rules and decisions apply.

## Goal

`docs/project/` says everything prompts 02 to 05 implement, so each of
them can point at a contract and not at this set.

## Established facts

- `01-architecture.md` lists `POST .../checks/{check_id}` in the agent
  protocol and describes the check flow in three steps under Data flows;
  its desired-state entry shows no check.
- `02-domain-model.md` describes Check without `detail`.
- `03-scenario-format.md`, Check, says stderr goes to the operator only.
- `05-recording.md` says check results join the markers with item 10.
- `06-security.md` says a link cookie reaches the check under its
  deployment's routes.

## Scope

Docs only, in the house style of the files they extend.

- `01-architecture.md`:
  - Operator API: the three check routes and who may call each;
  - Agent protocol: the `GET .../checks/{check_id}` line, the `check`
    field of a desired-state entry, the body of the script answer and of
    the result, and the 409 and 404 answers;
  - Data flows, Check: the steps as decided, with the one-at-a-time rule,
    the timeout the API settles itself and the end of the deployment.
- `02-domain-model.md`: Check gains `detail`; say that one check is
  pending at a time and how a pending one is settled without the agent.
- `03-scenario-format.md`, Check: the 200 character and 4096 byte limits,
  that the agent kills the script at the deadline, and where stderr is
  kept.
- `05-recording.md`: the `check <status>: <message>` marker in Format and
  Writing, and an acceptance line for it.
- `06-security.md`: the check script is guarded like the seed; `detail`
  never reaches a trainee or viewer link.
- `07-roadmap.md`: item 10's three bullets match the decisions.

## Failure behaviour

A sentence the decisions contradict is rewritten, not left beside the new
one.

## Acceptance

`.venv/bin/pre-commit run --files` on every edited file passes;
`grep -nP '[^\x00-\x7F]'` prints nothing for them.
