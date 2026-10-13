# 10 - Smoke: the operator and trainee pages load on the stand

Read `promts/00-README.md` first; its ground rules apply. Then read
`make/tests/smoke.sh` and `docs/project/08-web-ui.md`.

## Goal

The smoke stand proves item 11 end to end: `web` runs in front of the
API, an operator logs in through it, and the trainee link opens the
terminal page.

## Established facts

- The stand starts the API on 8080 and serves the image and the bundle on
  8081; `make run-web` serves `web` on 8000.
- The stand's `admin` changes its password on the first login; the
  password, the key and the cookies never appear on a command line.
- `open_link` opens `/s/{token}` on the API and does not follow the
  redirect.

## Scope

`make/tests/smoke.sh` only.

- Start `web` against the stand's API, in its own process group, and stop
  it in `cleanup`.
- Log in through the login page with the cookie jar in `$TEMP_DIR`; the
  deployments page answers 200 and names the deployment.
- Open the trainee link through `web`: 303 to `/t/{deployment_id}`, and
  that page answers 200 with the cookie.
- A state-changing request without its CSRF token answers 403.
- The existing terminal, check and replay steps go through `web`'s relay.
- After the revoke, the trainee page answers as `08-web-ui.md` says.

## Failure behaviour

Any step that fails names itself and exits non-zero; `cleanup` still
runs; no process of the stand survives the script. No page body is
printed.

## Acceptance

- `make smoke` passes with KVM.
- `.venv/bin/pre-commit run --files make/tests/smoke.sh` passes.
- Item 11 is marked Done in `docs/project/07-roadmap.md` once this lands.
