from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from jatszoter_api.models import Role, State, User

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
        {"name": "beta", "role": "trainee", "password": "correct horse battery"},
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

    for password in ("beta", "admin", "correct horse battery"):
        response = api.post("/api/v1/auth/login", json={"name": "beta", "password": password})
        assert response.status_code == 401


def test_only_an_operator_reaches_users(sign_in: Callable[..., Any], api: TestClient) -> None:
    viewer = sign_in(name="beta", role=Role.VIEWER)
    body = {"name": "gamma", "role": "trainee"}

    assert viewer.request("POST", USERS, json=body).status_code == 403
    assert viewer.request("GET", USERS).status_code == 403
    assert api.get(USERS).status_code == 401
