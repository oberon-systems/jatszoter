import hashlib
import hmac
import logging
import secrets
import time
from collections.abc import Callable, Iterator
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session

from jatszoter_api.app import create_app
from jatszoter_api.auth import require_operator
from jatszoter_api.models import Role, State, User
from jatszoter_api.models import user as user_model
from jatszoter_api.models.secret import SIGNATURE_WINDOW_SECONDS
from jatszoter_api.models.user import LOGIN_FAILURES, UNCHANGED, WEAK, _device_mac
from jatszoter_api.settings import Settings

PASSWORD = "Correct horse battery"
NEW_PASSWORD = "Staple of the paddock"
PROBE = "/api/v1/probe"


@pytest.fixture
def app(session: Session, settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get(PROBE)
    def probe(user: Annotated[User, Depends(require_operator)]) -> dict[str, str]:
        return {"name": user.name}

    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


@pytest.fixture
def from_address(app: FastAPI) -> Callable[[str], TestClient]:
    """A client of the same app whose requests come from `address`."""
    return lambda address: TestClient(app, client=(address, 50000))


def add_user(
    session: Session,
    name: str = "alpha",
    role: Role = Role.OPERATOR,
    state: State = State.ENABLED,
    changed: bool = True,
) -> User:
    user = User(
        session=session,
        name=name,
        role=role,
        state=state,
        password=PASSWORD,
        password_changed=int(time.time()) if changed else 0,
    )
    user.save()
    return user


def login(client: TestClient, name: str, password: str, **extra: str) -> Response:
    response: Response = client.post(
        "/api/v1/auth/login", json={"name": name, "password": password, **extra}
    )
    return response


class Operator:
    def __init__(self, client: TestClient, answer: dict[str, Any]) -> None:
        self.client = client
        self.id = answer["user_id"]
        self.use(answer["credential"])

    def use(self, credential: dict[str, Any]) -> None:
        self.credential = credential["credential"]
        self.key = credential["key"]

    def call(
        self, method: str, path: str, signed_path: str | None = None, skew: int = 0
    ) -> Response:
        timestamp = str(int(time.time()) + skew)
        nonce = secrets.token_hex(16)
        message = "\n".join(
            (timestamp, nonce, method, signed_path or path, hashlib.sha256(b"").hexdigest())
        )
        headers = {
            "X-Owner": self.id,
            "X-Credential": self.credential,
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": hmac.new(
                self.key.encode(), message.encode(), hashlib.sha256
            ).hexdigest(),
        }
        response: Response = self.client.request(method, path, headers=headers)
        return response


def logged_in(client: TestClient, name: str = "alpha") -> Operator:
    response = login(client, name, PASSWORD)
    assert response.status_code == 200
    return Operator(client, response.json())


def test_the_admin_is_created_once(session: Session, settings: Settings) -> None:
    create_app(settings)
    create_app(settings)

    admin = User(session=session).load(name="admin")
    assert User._load(session, role=Role.OPERATOR) == [admin]
    assert admin.state is State.ENABLED
    assert admin.password_changed == 0
    assert admin.check_password("admin")


def test_no_admin_is_created_while_an_operator_exists(session: Session, settings: Settings) -> None:
    add_user(session)

    create_app(settings)

    with pytest.raises(IndexError):
        User(session=session).load(name="admin")


def test_the_app_logs_the_admin_once_and_never_its_password(
    session: Session, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="jatszoter_api"):
        create_app(settings)
        create_app(settings)

    records = [record for record in caplog.records if record.name == "jatszoter_api.app"]
    stored = User(session=session).load(name="admin")
    assert stored.password is not None
    assert len(records) == 1
    assert "password admin" not in records[0].getMessage()
    assert stored.password not in caplog.text


def test_the_first_login_demands_a_new_password(client: TestClient, refused: Any) -> None:
    response = login(client, "admin", "admin")

    assert response.status_code == 403
    refused(response, "password-change-required")


def test_a_changed_password_gives_a_credential_operator_routes_accept(
    client: TestClient,
) -> None:
    response = login(client, "admin", "admin", new_password=NEW_PASSWORD)
    assert response.status_code == 200
    operator = Operator(client, response.json())

    assert operator.call("GET", PROBE).json() == {"name": "admin"}
    assert login(client, "admin", "admin").status_code == 401
    assert login(client, "admin", NEW_PASSWORD).status_code == 200


def test_a_refresh_promotes_the_generation(client: TestClient, session: Session) -> None:
    user = add_user(session)
    operator = logged_in(client)
    old = operator.credential

    response = operator.call("POST", "/api/v1/auth/credential")
    assert response.status_code == 200
    operator.use(response.json())

    assert operator.credential != old
    assert operator.call("GET", PROBE).status_code == 200
    session.expire_all()
    assert user.secret is not None
    assert user.secret.generation == int(operator.credential.split(":")[1])
    assert user.secret.pending_generation is None


def test_a_wrong_password_and_an_unknown_user_answer_alike(
    client: TestClient, session: Session, refused: Any
) -> None:
    add_user(session)

    wrong = login(client, "alpha", "not the password")
    unknown = login(client, "beta", PASSWORD)

    assert wrong.status_code == unknown.status_code == 401
    refused(wrong, "not-authorized")
    refused(unknown, "not-authorized")


def test_too_many_failures_are_throttled_even_with_the_right_password(
    client: TestClient, session: Session
) -> None:
    add_user(session)
    for _ in range(LOGIN_FAILURES):
        assert login(client, "alpha", "not the password").status_code == 401

    assert login(client, "alpha", PASSWORD).status_code == 429


def test_a_success_clears_the_failures(client: TestClient, session: Session) -> None:
    add_user(session)
    for _ in range(LOGIN_FAILURES - 1):
        login(client, "alpha", "not the password")
    assert login(client, "alpha", PASSWORD).status_code == 200

    for _ in range(LOGIN_FAILURES - 1):
        login(client, "alpha", "not the password")

    assert login(client, "alpha", PASSWORD).status_code == 200


def fail(client: TestClient, times: int = LOGIN_FAILURES, **extra: str) -> None:
    for _ in range(times):
        assert login(client, "alpha", "not the password", **extra).status_code == 401


def test_failures_from_one_address_leave_the_name_open_to_another(
    session: Session, from_address: Callable[[str], TestClient]
) -> None:
    add_user(session)
    attacker = from_address("192.0.2.1")
    fail(attacker)

    assert login(attacker, "alpha", PASSWORD).status_code == 429
    assert login(from_address("192.0.2.2"), "alpha", PASSWORD).status_code == 200


def test_failures_from_many_addresses_lock_the_name_but_not_a_known_device(
    session: Session,
    from_address: Callable[[str], TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_user(session)
    device = login(from_address("192.0.2.9"), "alpha", PASSWORD).json()["device"]
    monkeypatch.setattr(user_model, "NAME_FAILURES", 3)
    for host in range(1, 4):
        fail(from_address(f"192.0.2.{host}"), 1)

    fresh = from_address("192.0.2.10")
    assert login(fresh, "alpha", PASSWORD).status_code == 429
    assert login(fresh, "alpha", PASSWORD, device=device).status_code == 200


def test_a_device_has_a_limit_of_its_own(
    session: Session, from_address: Callable[[str], TestClient]
) -> None:
    add_user(session)
    browser = from_address("192.0.2.1")
    device = login(browser, "alpha", PASSWORD).json()["device"]
    fail(browser, device=device)

    assert login(browser, "alpha", PASSWORD, device=device).status_code == 429
    assert login(browser, "alpha", PASSWORD).status_code == 200


def tokens(user: User, other: str, keys: dict[str, str]) -> list[str]:
    """Another user's, an expired, a forged, an unknown kid's and malformed device tokens."""
    kid, master = next(iter(keys.items()))
    lapsed = str(int(time.time()) - 1)
    live = "9999999999"
    return [
        other,
        ".".join((kid, lapsed, "gamma", _device_mac(master, str(user.id), lapsed, "gamma"))),
        ".".join((kid, live, "delta", _device_mac(master, str(user.id), live, "gamma"))),
        ".".join(("omega", live, "gamma", _device_mac(master, str(user.id), live, "gamma"))),
        f"{kid}.\u00b2\u00b2.gamma.delta",
        "gamma",
    ]


def test_a_token_not_issued_to_the_user_counts_as_none(
    session: Session, settings: Settings, from_address: Callable[[str], TestClient]
) -> None:
    user = add_user(session)
    add_user(session, "beta")
    other = login(from_address("192.0.2.100"), "beta", PASSWORD).json()["device"]

    for host, token in enumerate(tokens(user, other, settings.master_keys), start=1):
        client = from_address(f"192.0.2.{host}")
        fail(client, device=token)
        assert login(client, "alpha", PASSWORD, device=token).status_code == 429, token


def test_ipv6_addresses_count_by_their_64(
    session: Session, from_address: Callable[[str], TestClient]
) -> None:
    add_user(session)
    fail(from_address("2001:db8::1"))

    assert login(from_address("2001:db8::2"), "alpha", PASSWORD).status_code == 429
    assert login(from_address("2001:db8:0:1::1"), "alpha", PASSWORD).status_code == 200


def test_a_disabled_user_is_refused(client: TestClient, session: Session) -> None:
    user = add_user(session)
    operator = logged_in(client)
    user.state = State.DISABLED
    user.save()

    assert login(client, "alpha", PASSWORD).status_code == 401
    assert operator.call("GET", PROBE).status_code == 401


def test_a_trainee_is_forbidden_operator_routes(client: TestClient, session: Session) -> None:
    add_user(session, "beta", Role.TRAINEE)
    trainee = logged_in(client, "beta")

    assert trainee.call("POST", "/api/v1/auth/credential").status_code == 200
    assert trainee.call("GET", PROBE).status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        {"signed_path": "/api/v1/auth/credential"},
        {"skew": -SIGNATURE_WINDOW_SECONDS - 1},
    ],
)
def test_a_request_signed_for_something_else_is_refused(
    client: TestClient, session: Session, change: dict[str, Any]
) -> None:
    add_user(session)
    operator = logged_in(client)

    assert operator.call("GET", PROBE, **change).status_code == 401


def test_the_query_is_part_of_the_signature(client: TestClient, session: Session) -> None:
    add_user(session)
    operator = logged_in(client)
    queried = f"{PROBE}?role=trainee"

    assert operator.call("GET", queried, signed_path=PROBE).status_code == 401
    assert operator.call("GET", queried).status_code == 200


@pytest.mark.parametrize("owner", ["", "alpha", "00000000-0000-4000-8000-000000000000"])
def test_a_request_for_another_owner_is_refused(
    client: TestClient, session: Session, owner: str
) -> None:
    add_user(session)
    operator = logged_in(client)
    operator.id = owner

    assert operator.call("GET", PROBE).status_code == 401


@pytest.mark.parametrize(
    ("new_password", "code", "logged"),
    [
        ("Sh0rt!", "password-weak", WEAK),
        ("onlylowercaseletters", "password-weak", WEAK),
        ("Lettersonlyhere", "password-weak", WEAK),
        (PASSWORD, "password-unchanged", UNCHANGED),
    ],
)
def test_a_weak_new_password_is_refused(
    client: TestClient,
    session: Session,
    refused: Any,
    new_password: str,
    code: str,
    logged: str,
) -> None:
    add_user(session, changed=False)

    response = login(client, "alpha", PASSWORD, new_password=new_password)

    assert response.status_code == 422
    refused(response, code, logged)
    assert login(client, "alpha", PASSWORD).status_code == 403


def test_three_classes_of_four_make_a_new_password(client: TestClient, session: Session) -> None:
    add_user(session, changed=False)

    assert login(client, "alpha", PASSWORD, new_password="lower case 12345").is_success


def test_a_malformed_login_is_refused(client: TestClient) -> None:
    body = {"name": "admin", "password": "admin", "extra": 1}

    assert client.post("/api/v1/auth/login", json=body).status_code == 422


def test_a_logout_ends_the_credential(client: TestClient, session: Session) -> None:
    add_user(session)
    operator = logged_in(client)

    assert operator.call("POST", "/api/v1/auth/logout").status_code == 204
    assert operator.call("GET", PROBE).status_code == 401


def test_me_answers_the_signed_users_id_name_and_role(client: TestClient, session: Session) -> None:
    user = add_user(session, role=Role.VIEWER)
    signed = logged_in(client)

    response = signed.call("GET", "/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json() == {"id": str(user.id), "name": "alpha", "role": "viewer"}


def test_me_without_a_signature_is_refused(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401


def test_a_superseded_user_generation_is_refused_and_disables_nobody(
    client: TestClient, session: Session
) -> None:
    user = add_user(session)
    operator = logged_in(client)
    old = {"credential": operator.credential, "key": operator.key}
    fresh = operator.call("POST", "/api/v1/auth/credential").json()
    operator.use(fresh)
    assert operator.call("GET", PROBE).status_code == 200

    operator.use(old)
    assert operator.call("GET", PROBE).status_code == 401

    session.expire_all()
    assert User(session=session).load(id=user.id).state is State.ENABLED
    operator.use(fresh)
    assert operator.call("GET", PROBE).status_code == 200
