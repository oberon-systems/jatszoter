import hashlib
import hmac
import logging
import time
from collections.abc import Iterator
from typing import Annotated, Any

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session

from jatszoter_api.app import create_app
from jatszoter_api.auth import require_operator
from jatszoter_api.models import Role, State, User
from jatszoter_api.models.secret import SIGNATURE_WINDOW_SECONDS
from jatszoter_api.models.user import LOGIN_FAILURES

PASSWORD = "correct horse battery"
NEW_PASSWORD = "staple of the paddock"
PROBE = "/api/v1/probe"


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app = create_app()

    @app.get(PROBE)
    def probe(user: Annotated[User, Depends(require_operator)]) -> dict[str, str]:
        return {"name": user.name}

    with TestClient(app) as client:
        yield client


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
        password_changed=int(time.time()) if changed else 0,
    )
    user.set_password(PASSWORD)
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
        message = "\n".join(
            (timestamp, method, signed_path or path, hashlib.sha256(b"").hexdigest())
        )
        headers = {
            "X-Owner": self.id,
            "X-Credential": self.credential,
            "X-Timestamp": timestamp,
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


def test_the_admin_is_created_once(session: Session) -> None:
    first = User.ensure_admin(session)
    second = User.ensure_admin(session)

    assert first is not None
    assert first.role is Role.OPERATOR
    assert first.state is State.ENABLED
    assert first.password_changed == 0
    assert first.check_password("admin")
    assert second is None


def test_no_admin_is_created_while_an_operator_exists(session: Session) -> None:
    add_user(session)

    assert User.ensure_admin(session) is None
    assert User.by_name(session, "admin") is None


def test_the_app_logs_the_admin_once_and_never_its_password(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="jatszoter_api"):
        create_app()
        create_app()

    records = [record for record in caplog.records if record.name == "jatszoter_api.app"]
    admin = User.by_name(session, "admin")
    assert admin is not None and admin.password is not None
    assert len(records) == 1
    assert "password admin" not in records[0].getMessage()
    assert admin.password not in caplog.text


def test_the_first_login_demands_a_new_password(client: TestClient) -> None:
    response = login(client, "admin", "admin")

    assert response.status_code == 403
    assert response.json() == {"detail": "password change required"}


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
    client: TestClient, session: Session
) -> None:
    add_user(session)

    wrong = login(client, "alpha", "not the password")
    unknown = login(client, "beta", PASSWORD)

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "not authorized"}


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


@pytest.mark.parametrize("owner", ["", "alpha", "00000000-0000-4000-8000-000000000000"])
def test_a_request_for_another_owner_is_refused(
    client: TestClient, session: Session, owner: str
) -> None:
    add_user(session)
    operator = logged_in(client)
    operator.id = owner

    assert operator.call("GET", PROBE).status_code == 401


@pytest.mark.parametrize("new_password", ["short", PASSWORD])
def test_a_weak_new_password_is_refused(
    client: TestClient, session: Session, new_password: str
) -> None:
    add_user(session, changed=False)

    assert login(client, "alpha", PASSWORD, new_password=new_password).status_code == 422
    assert login(client, "alpha", PASSWORD).status_code == 403


def test_a_malformed_login_is_refused(client: TestClient) -> None:
    body = {"name": "admin", "password": "admin", "extra": 1}

    assert client.post("/api/v1/auth/login", json=body).status_code == 422
