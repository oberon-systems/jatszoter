# 02 - API: users and links

Read `promts/00-README.md` first; its ground rules apply. Then read Links in
`docs/project/04-console.md`, Entities and Storage rules in
`docs/project/02-domain-model.md` and Operator API in
`docs/project/01-architecture.md`.

## Goal

An operator creates users, every deployment is created with its trainee
link, and links are issued, listed and revoked. Nothing opens a link yet.

## Established facts

- `models/user.py` has `User` with `name`, `role` (`Role`), `comment`,
  `state`, `password`; `routes/auth.py` loads a user by name to log in, and
  `check_password` is false for a user without a password.
- `models/link.py` has `Link` with `deployment_id`, `user_id`,
  `token_hash`, `opened_at`, `revoked_at`; a new instance mints its token
  and `token` returns it, a loaded one returns `None`.
- `Deployment.links` lists a deployment's links, and `transition` stamps
  `revoked_at` on all of them at a terminal status.
- `routes/deployments.py` creates a deployment from `scenario_id` and an
  optional `agent_id`; its router requires a signed operator.
- `Base._save` turns a broken unique key into `ConflictError`, a 409.
- `smoke.sh` creates its deployment with `scenario_id` alone.

## Scope

- `models/user.py` and `migrations/versions/v0001_initial.py`:
  `users.name` is unique.
- `models/link.py`: `revoke()` stamps `revoked_at` once and saves; a
  repeated call changes nothing.
- `routes/users.py`, mounted under `/api/v1`, signed operator:
  - `POST /users` with `name` (1 to 64 characters), `role` and an optional
    `comment`: stores the user `enabled`, without a password, answers 201
    with id, name, role, comment, state and `created_at`;
  - `GET /users`, newest first, with an optional `role` filter.
- `routes/deployments.py`:
  - `POST /deployments` requires `trainee_id`; a user that is unknown, not
    `enabled` or not a trainee is refused like an unusable `agent_id`. The
    route saves the deployment, then its trainee link, and answers the
    deployment's fields plus `link`: id, `user_id` and `token`. A link that
    cannot be saved stops the deployment and passes the error on;
  - `POST /deployments/{id}/links` with `user_id`: 201 with the link and
    its `token`. Refused with 409 for a terminal deployment, and for a
    trainee while the deployment holds a live trainee link; an unknown or
    disabled user is refused like above;
  - `GET /deployments/{id}/links`: id, `user_id`, the user's role,
    `created_at`, `opened_at`, `revoked_at`; never a token or its hash.
- `routes/links.py`, mounted under `/api/v1`, signed operator:
  `POST /links/{id}/revoke` answers the link as the list does; 404 for an
  unknown id.
- `make/tests/smoke.sh`: create a trainee user and pass `trainee_id` on
  create; nothing else changes.

## Failure behaviour

- A duplicate user name is 409; a body with an unknown field or role is
  422.
- The token is returned once and never logged; no response but the one
  that creates a link carries it.
- A failed link save leaves no PENDING deployment waiting for an agent.

## Tests

- `tests/test_users_api.py`:
  - positive: create and list, the role filter, a created user is
    `enabled`;
  - negative: a duplicate name is 409; a user without a password cannot
    log in; a non-operator gets 403.
- `tests/test_links_api.py`:
  - positive: create returns the trainee link with a token whose sha256 is
    the stored hash; issue for an operator and a viewer; list shows roles
    and no token; revoke stamps once and a second revoke keeps the time; a
    new trainee link is issued after the first is revoked;
  - negative: create without `trainee_id` is 422; a viewer or disabled
    user as trainee is refused; a second live trainee link is 409; issue on
    a terminal deployment is 409; revoke of an unknown id is 404.
- `tests/test_deployments_api.py` and every other test that creates a
  deployment pass a trainee; `test_head_matches_the_models` still passes.

## Acceptance

`.venv/bin/pytest -q packages/api` green; `make smoke` passes;
`.venv/bin/pre-commit run --files` on every edited file passes.
