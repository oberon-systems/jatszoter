# 02 - API: operator login and signed operator requests

Read `promts/00-README.md` first; its ground rules apply. Then read
`docs/project/06-security.md` (Credentials) and the Operator API section of
`docs/project/01-architecture.md`.

## Goal

Users log in with a name and password and receive a credential and signing
key, the same kind an agent claims. Operator routes depend on
`require_operator`, which checks a signed request the way `require_agent`
does. No operator route exists yet besides the auth ones; later prompts add
them.

## Established facts

- `models/secret.py`: `SecretOwner` gives `issue(ttl)` and `verify(...)`;
  `User` already inherits it. `Credential.sign` signs
  `ts\nMETHOD\npath\nsha256(body)`.
- `auth.py::require_agent` parses `X-Credential` as `kid:generation:expires_at`
  with the owner id taken from the path.
- `routes/agents.py::CredentialOut` is the credential answer; reuse it.
- `models/_cache.py::Cached` is the Valkey or fakeredis client.

## Scope

- `models/user.py`: `password_hash: str | None`,
  `password_change_required: bool`; `set_password(plain)`,
  `check_password(plain) -> bool`; classmethods `by_name(session, name)` and
  `ensure_admin(session)`.
- Password hashing with `hashlib.scrypt` from the standard library: random
  16-byte salt, parameters stored in the hash string
  (`scrypt$n$r$p$salt_hex$hash_hex`), `hmac.compare_digest` on check. No new
  dependency.
- Alembic revision `v0002_user_password`.
- `app.py`: after migrating, `User.ensure_admin` creates `admin` (role
  operator, `enabled`, password `admin`, `password_change_required=True`) when
  no operator user exists; logs once that it did, never the password.
- `auth.py`: factor the signature check shared with `require_agent`; add
  `require_user` and `require_operator` reading `X-Owner`, `X-Credential`,
  `X-Timestamp`, `X-Signature`. A non-operator user gets 403, anything that
  does not verify gets 401.
- `routes/auth.py`, mounted under `/api/v1/auth`:
  - `POST /login`, body `{name, password, new_password?}` (strict model):
    - wrong name or password: 401 `not authorized`, and for an unknown name
      still run one scrypt so timing does not tell the two apart;
    - `password_change_required` and no `new_password`: 403
      `password change required`;
    - `new_password`: at least 12 characters and different from the old one,
      else 422; stored, flag cleared;
    - success: 200 `{user_id, credential: CredentialOut}` from
      `user.issue(CREDENTIAL_SECONDS)`;
    - throttle: failures counted in the cache under
      `login:<sha256 of the name>` with a 15-minute TTL; at 10 the endpoint
      answers 429 without checking the password. A success clears it.
  - `POST /credential`, signed by a user: a refreshed `CredentialOut`.
- Tests in `packages/api/tests/test_auth.py`.

## Failure behaviour

A disabled user gets 401 on login and on signed requests. Nothing about which
check failed reaches the response body or the log.

## Tests

Positive: admin is created once and only when no operator exists; first login
demands a new password; login with `new_password` returns a credential that
signs a request `require_operator` accepts; refresh works and promotes the
generation like an agent's.

Negative: wrong password and unknown user answer identically; the 11th
failure within the window gets 429 even with the right password; a trainee's
signed request gets 403 from `require_operator`; a request signed for another
path, or with a stale timestamp, gets 401; a short or unchanged
`new_password` gets 422; `test_migrations.py` still passes.

## Acceptance

`.venv/bin/pytest -q packages/api` green; pre-commit clean on every changed
file.
