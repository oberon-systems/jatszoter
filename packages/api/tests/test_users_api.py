import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from jatszoter_api.models import Link, Role, State, User

USERS = "/api/v1/users"


def test_a_created_user_is_enabled_and_listed(operator: Any, session: Session) -> None:
    response = operator.request(
        "POST", USERS, json={"name": "beta", "role": "trainee", "comment": "first shift"}
    )

    assert response.status_code == 201
    created = response.json()
    assert set(created) == {"id", "name", "role", "comment", "state", "created_at"}
    assert (created["name"], created["role"], created["comment"]) == (
        "beta",
        "trainee",
        "first shift",
    )
    assert created["state"] == State.ENABLED
    assert User(session=session).load(name="beta").password is None
    assert created in operator.request("GET", USERS).json()


def test_the_list_filters_by_role_newest_first(operator: Any, session: Session) -> None:
    for name, role, created_at in (
        ("beta", Role.TRAINEE, 100),
        ("gamma", Role.TRAINEE, 200),
        ("delta", Role.VIEWER, 300),
    ):
        User(session=session, name=name, role=role, created_at=created_at).save()

    trainees = operator.request("GET", USERS, params={"role": "trainee"})
    every = operator.request("GET", USERS)

    assert [row["name"] for row in trainees.json()] == ["gamma", "beta"]
    assert [row["name"] for row in every.json()][-3:] == ["delta", "gamma", "beta"]
    assert operator.request("GET", USERS, params={"role": "root"}).status_code == 422


def test_a_duplicate_name_conflicts(operator: Any, session: Session) -> None:
    body = {"name": "beta", "role": "trainee"}
    assert operator.request("POST", USERS, json=body).status_code == 201

    response = operator.request("POST", USERS, json={**body, "role": "viewer"})

    assert response.status_code == 409
    assert len(User._load(session, name="beta")) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"name": "beta", "role": "root"},
        {"name": "beta", "role": "trainee", "password": "Correct horse battery"},
        {"name": "beta", "role": "trainee", "state": "disabled"},
        {"name": "", "role": "trainee"},
        {"name": "b" * 65, "role": "trainee"},
        {"name": "beta"},
    ],
)
def test_a_malformed_user_is_unprocessable(
    operator: Any, session: Session, body: dict[str, str]
) -> None:
    assert operator.request("POST", USERS, json=body).status_code == 422
    assert User._load(session, name="beta") == []


def test_a_user_without_a_password_cannot_log_in(operator: Any, api: TestClient) -> None:
    assert operator.request("POST", USERS, json={"name": "beta", "role": "operator"}).is_success

    for password in ("beta", "admin", "Correct horse battery"):
        response = api.post("/api/v1/auth/login", json={"name": "beta", "password": password})
        assert response.status_code == 401


def test_only_an_operator_reaches_users(sign_in: Callable[..., Any], api: TestClient) -> None:
    viewer = sign_in(name="beta", role=Role.VIEWER)
    body = {"name": "gamma", "role": "trainee"}

    assert viewer.request("POST", USERS, json=body).status_code == 403
    assert viewer.request("GET", USERS).status_code == 403
    assert api.get(USERS).status_code == 401


def stored(session: Session, name: str = "beta", role: Role = Role.TRAINEE) -> User:
    user = User(session=session, name=name, role=role, state=State.ENABLED)
    user.save()
    return user


def login(api: TestClient, name: str, password: str, **extra: str) -> Any:
    return api.post("/api/v1/auth/login", json={"name": name, "password": password, **extra})


def test_a_user_is_disabled_and_enabled_again(
    operator: Any, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = stored(session)
    link = Link(session=session, deployment_id=uuid.uuid4(), user_id=user.id)
    link.save()
    revoked: list[uuid.UUID] = []
    monkeypatch.setattr(operator.client.app.state.console_hub, "revoke", revoked.append)

    off = operator.request("POST", f"{USERS}/{user.id}/state", json={"state": "disabled"})
    assert off.status_code == 200
    assert off.json()["state"] == State.DISABLED
    assert revoked == [link.id]

    on = operator.request("POST", f"{USERS}/{user.id}/state", json={"state": "enabled"})
    assert on.status_code == 200
    assert on.json()["state"] == State.ENABLED
    assert revoked == [link.id]


def test_the_last_enabled_operator_stays_enabled(operator: Any, session: Session) -> None:
    admin = User(session=session).load(name="admin")
    assert operator.request(
        "POST", f"{USERS}/{admin.id}/state", json={"state": "disabled"}
    ).is_success

    response = operator.request("POST", f"{USERS}/{operator.id}/state", json={"state": "disabled"})

    assert response.status_code == 409
    assert response.json()["detail"] == "cannot disable the last enabled operator"
    session.expire_all()
    assert User(session=session).load(name="alpha").state is State.ENABLED


@pytest.mark.parametrize("state", ["", "pending", None])
def test_an_unknown_state_is_unprocessable(operator: Any, session: Session, state: Any) -> None:
    user = stored(session)

    response = operator.request("POST", f"{USERS}/{user.id}/state", json={"state": state})

    assert response.status_code == 422


def test_an_unknown_user_is_not_found(operator: Any) -> None:
    unknown = f"{USERS}/{uuid.uuid4()}"

    state = {"state": "disabled"}
    assert operator.request("POST", f"{unknown}/state", json=state).status_code == 404
    password = {"password": "One time password"}
    assert operator.request("POST", f"{unknown}/password", json=password).status_code == 404


def test_a_one_time_password_must_be_changed_at_the_next_login(
    operator: Any, api: TestClient, session: Session
) -> None:
    user = stored(session)

    response = operator.request(
        "POST", f"{USERS}/{user.id}/password", json={"password": "One time password"}
    )

    assert response.status_code == 204
    refused = login(api, "beta", "One time password")
    assert refused.status_code == 403
    assert refused.json()["detail"] == "password change required"
    assert login(api, "beta", "One time password", new_password="Staple of the paddock").is_success


@pytest.mark.parametrize(
    "body",
    [
        {"password": "short"},
        {"password": "onlylowercaseletters"},
        {"password": ""},
        {},
        {"pw": "x" * 12},
    ],
)
def test_a_weak_or_malformed_one_time_password_is_refused(
    operator: Any, api: TestClient, session: Session, body: dict[str, str]
) -> None:
    user = stored(session)

    response = operator.request("POST", f"{USERS}/{user.id}/password", json=body)

    assert response.status_code == 422
    session.expire_all()
    assert User(session=session).load(id=user.id).password is None


def test_only_an_operator_changes_a_users_state_or_password(
    sign_in: Callable[..., Any], api: TestClient, session: Session
) -> None:
    viewer = sign_in(name="gamma", role=Role.VIEWER)
    user = stored(session)
    one = f"{USERS}/{user.id}"

    assert viewer.request("POST", f"{one}/state", json={"state": "disabled"}).status_code == 403
    password = {"password": "One time password"}
    assert viewer.request("POST", f"{one}/password", json=password).status_code == 403
    assert api.post(f"{one}/state", json={"state": "disabled"}).status_code == 401
