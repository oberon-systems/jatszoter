# 05 - API: the routes the screens need

Read `promts/00-README.md` first; its ground rules apply. Then read
`docs/project/08-web-ui.md`, `docs/project/01-architecture.md`,
`docs/project/02-domain-model.md` and the route and model each change
touches.

## Goal

Every API call `08-web-ui.md` lists for a screen exists, is signed
(operator) unless the document says otherwise, and is tested.

## Established facts

- Before this prompt an operator can neither list agents nor enable one
  through the API, and can neither enable nor disable a user.
- What the logs, storage management and results screens call is whatever
  prompt 02 settled; it is in `08-web-ui.md` and nowhere else.

## Scope

`packages/api/src/jatszoter_api` and its tests, plus the route list in
`docs/project/01-architecture.md`.

- One route per missing call, in the route module of its resource; ask
  before adding a module or a model.
- Lists answer what the board shows and no more: no token, hash, key or
  file store URL in any answer.
- A change of state is a domain method on the model; the route stays thin.

## Failure behaviour

An unknown id answers 404, a refused state 409, a non-operator 403, each
through a `DomainError`. A call the document lists but the model cannot
serve is reported, not approximated.

## Acceptance

- `.venv/bin/pytest -q packages/api` passes, with a positive and a
  negative test for every new route.
- `make smoke` passes.
- `.venv/bin/pre-commit run --files <paths>` passes.
