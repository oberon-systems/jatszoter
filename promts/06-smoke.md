# 06 - Smoke: a trainee types, take control cuts it

Read `promts/00-README.md` first; its ground rules apply. Then read
`make/tests/smoke.sh`, `make/tests/smoke.mk` and Links, Take control and
Resize in `docs/project/04-console.md`.

## Goal

The smoke stand proves item 8 end to end: the trainee link opens a
terminal that types into the guest, taking control cuts the trainee's
input and gives the operator's a way in, a resize reaches the guest, and a
revoked link opens nothing.

## Established facts

- After prompt 02 `smoke.sh` creates a trainee user and reads the
  deployment from the create response, which also carries `link.id` and
  `link.token`.
- `viewer_py` is a Python one-shot run with `$VENV/bin/python -I -c`; it
  signs an operator upgrade from its environment and prints counts only.
- After the bootstrap the serial getty logs the trainee in, so the console
  is a shell prompt at READY.
- The shell echoes what is typed, so a typed command is not proof that it
  ran: `printf` of `$((40 + 2))` is typed as text and printed as `42`.
- `printf` is a shell builtin, so a value passed to it reaches no process
  argument list; `curl -K -` reads its URL from stdin.

## Scope

`make/tests/smoke.sh` only, between the console checks and the stop.

- Open the link: `GET /s/$token` through `curl -K -`, without following
  the redirect. It must answer 303 with `Location: /t/$deployment` and a
  `jatszoter_link` cookie that is HttpOnly, Secure, SameSite=Strict and
  scoped to the deployment's path. The cookie value is kept in a variable.
- A `terminal` step, one Python one-shot given the URL, the cookie and the
  operator's owner, credential and key through its environment. It opens
  a trainee socket with the cookie and an operator socket with the
  signature, and checks in order, each within 30 s:
  - the trainee socket receives an offset and `{"control": "trainee"}`;
  - the trainee types a `printf` whose arithmetic the guest must expand;
    the expanded text arrives on the trainee socket;
  - it takes control with a signed `POST .../control`; both sockets
    receive `{"control": "operator"}`;
  - the trainee types a second such line; its expanded text does not
    arrive within 5 s;
  - the operator types a third; its expanded text arrives;
  - the operator sends `{"cols": 100, "rows": 30}`, then types
    `stty size`; `30 100` arrives.
  It prints the name of each check as it passes and never the bytes.
- Revoke the trainee link with a signed `POST /api/v1/links/$link/revoke`;
  opening the link again must answer 404.
- Neither `$TEMP_DIR/api.log` nor `$TEMP_DIR/agent.log` contains the link
  token or the cookie value; the patterns reach `grep` through `-f` and a
  process substitution.
- Stop and the checks after it are unchanged.

## Failure behaviour

Any check that times out names itself and exits non-zero; `cleanup` still
runs; no QEMU survives the script. The link token, the cookie and the key
never appear on a command line.

## Acceptance

- `make smoke` passes with KVM.
- With the control route's holder check removed from the API (tried
  locally and reverted), the smoke run fails at the trainee's second line.
- `.venv/bin/pre-commit run --files make/tests/smoke.sh` passes.
