# 02 - API: a check is requested, read and timed out

Read `promts/00-README.md` first; its ground rules apply. Then read the
Check parts of `docs/project/01-architecture.md`,
`docs/project/02-domain-model.md` and `docs/project/06-security.md`.

## Goal

An operator or a trainee asks for a check and reads how it ended; a check
nobody answers ends by itself.

## Established facts

- `models/check.py` holds the Check table with `deployment_id`, `link_id`,
  `user_id`, `status`, `exit_code`, `message`, `finished_at`, and only
  `load`.
- `Deployment.checks` loads a deployment's checks; `Deployment.transition`
  revokes links and closes recordings on a terminal target.
- `require_deployment_operator` in `auth.py` lets a signed operator or an
  operator link of the deployment through, and refuses a cookie request
  with a foreign `Origin`; the control route uses it.
- A deployment carries `check_seconds`.

## Scope

- `models/check.py`: the `detail` column, `save`, and the domain methods
  the routes need - settling a check with its result, and settling a
  pending one that is overdue. `migrations/versions/v0001_initial.py`
  gains the column.
- `models/deployment.py`: a terminal transition settles a pending check as
  `error` with the message `deployment ended`.
- `auth.py`: a dependency for the parties of a deployment - a signed
  operator, or a link of that deployment with its user's role - with the
  same `Origin` rule as the control route.
- `routes/checks.py`, new, registered in `routes/__init__.py` and `app.py`:
  - `POST /deployments/{id}/checks`, 201 with the stored check;
  - `GET /deployments/{id}/checks`, oldest first;
  - `GET /deployments/{id}/checks/{check_id}`.
- An answer carries `id`, `status`, `exit_code`, `message`, `created_at`,
  `finished_at`, and `detail` for an operator only: for a trainee or
  viewer link the key is absent, not null.
- `link_id` is the link the request came through, `user_id` its user or
  the signed operator.

## Failure behaviour

- A request while the deployment is not READY, and one while a check is
  pending, answer 409. A viewer link's request answers 403.
- A pending check older than `check_seconds` plus 30 seconds becomes
  `timeout` before any of the three routes answers; the request that
  finds it may then store a new one.
- An unknown deployment or check, and a check of another deployment,
  answer 404. A link of another deployment gets what it gets on the
  control route.

## Tests

`tests/test_checks_api.py`, and `tests/test_deployments.py` for the
transition:

- positive: a trainee link and a signed operator each store a pending
  check with the right `link_id` and `user_id`; the list is oldest first;
  an overdue pending check reads as `timeout` and frees the next request;
  the operator sees `detail` and the trainee link has no such key;
- negative: a second request while one is pending is 409; a deployment
  that is not READY is 409; a viewer link is 403; an unsigned request, a
  foreign `Origin` and a link of another deployment are refused; a
  deployment that ends leaves no pending check.

## Acceptance

`.venv/bin/pytest -q packages/api` green; `make smoke` passes;
`.venv/bin/pre-commit run --files` on every edited file passes.
