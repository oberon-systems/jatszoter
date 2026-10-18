import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from jatszoter_web.cookies import Operator

Answer = Callable[..., None]
SignIn = Callable[..., Operator]

HTMX = {"hx-request": "true"}
PASSWORD = "Correct-horse-alpha"


def posted(calls: list[httpx.Request]) -> list[httpx.Request]:
    return [call for call in calls if call.method == "POST"]


def test_the_list_shows_every_user(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any]
) -> None:
    sign_in()
    user_id = stand["trainee"]["id"]

    page = client.get("/users")

    assert page.status_code == 200
    for text in ("beta", "trainee", "group one", "enabled", "2025-10-09 08:53"):
        assert text in page.text
    assert f'hx-get="/users/{user_id}/password"' in page.text
    assert f'hx-get="/users/{user_id}/state"' in page.text


@pytest.mark.usefixtures("stand")
def test_the_role_filter_reaches_the_api_and_swaps_the_table(
    client: TestClient, sign_in: SignIn, answer: Answer, calls: list[httpx.Request]
) -> None:
    sign_in()

    table = client.get("/users?role=trainee", headers={**HTMX, "hx-target": "users-table"})
    client.get("/users?role=bogus")
    answer("GET", "/api/v1/users", [])
    empty = client.get("/users?role=viewer")

    assert table.text.lstrip().startswith('<div id="users-table"')
    assert [call.url.params.get("role") for call in calls] == ["trainee", None, "viewer"]
    assert "No users match the filter" in empty.text
    assert "Nobody has the viewer role yet." in empty.text


@pytest.mark.usefixtures("stand")
def test_a_refused_list_shows_the_refusal(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    sign_in()
    answer("GET", "/api/v1/users", {"detail": "users are not listed"}, 422)

    page = client.get("/users")

    assert page.status_code == 422
    assert "users are not listed (422)" in page.text


@pytest.mark.usefixtures("stand")
def test_a_new_user_lands_on_the_list(
    client: TestClient, sign_in: SignIn, answer: Answer, calls: list[httpx.Request]
) -> None:
    who = sign_in()
    answer("POST", "/api/v1/users", {"name": "epsilon"}, 201)
    form = {"csrf_token": who.csrf, "name": "epsilon", "role": "trainee", "comment": " "}

    dialog = client.get("/users/new", headers=HTMX)
    created = client.post("/users", data=form, headers=HTMX)
    shown = client.get("/users")

    assert dialog.text.lstrip().startswith("<dialog")
    assert created.status_code == 204
    assert created.headers["hx-redirect"] == "/users"
    assert json.loads(posted(calls)[0].content) == {
        "name": "epsilon",
        "role": "trainee",
        "comment": None,
    }
    assert "User created" in shown.text
    assert "epsilon is enabled; set a one-time password so they can log in." in shown.text


@pytest.mark.parametrize(
    ("status", "detail", "field", "text"),
    [
        (409, "name epsilon is taken", "name", "name epsilon is taken"),
        (422, [{"loc": ["body", "role"], "msg": "role is not valid"}], "role", "role is not valid"),
    ],
)
@pytest.mark.usefixtures("stand")
def test_a_refused_user_shows_the_refusal_next_to_its_field(
    client: TestClient,
    sign_in: SignIn,
    answer: Answer,
    status: int,
    detail: Any,
    field: str,
    text: str,
) -> None:
    who = sign_in()
    answer("POST", "/api/v1/users", {"detail": detail}, status)
    form = {"csrf_token": who.csrf, "name": "epsilon", "role": "trainee"}

    dialog = client.post("/users", data=form, headers=HTMX)

    assert dialog.status_code == status
    assert dialog.text.lstrip().startswith("<dialog")
    assert f'<span class="field-error">{text}</span>' in dialog.text
    assert f"{text} ({status})" in dialog.text
    assert 'value="epsilon"' in dialog.text


def test_a_password_is_set_through_its_dialog(
    client: TestClient,
    sign_in: SignIn,
    stand: dict[str, Any],
    answer: Answer,
    calls: list[httpx.Request],
) -> None:
    who = sign_in()
    user_id = stand["trainee"]["id"]
    answer("POST", f"/api/v1/users/{user_id}/password", None, 204)
    form = {"csrf_token": who.csrf, "password": PASSWORD, "repeat_password": PASSWORD}

    dialog = client.get(f"/users/{user_id}/password", headers=HTMX)
    password_set = client.post(f"/users/{user_id}/password", data=form)
    shown = client.get("/users")

    assert "beta" in dialog.text
    assert password_set.status_code == 303
    assert json.loads(posted(calls)[0].content) == {"password": PASSWORD}
    assert "Password set" in shown.text
    assert "beta must change it at the next login." in shown.text


def test_passwords_that_differ_never_reach_the_api(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any], calls: list[httpx.Request]
) -> None:
    who = sign_in()
    form = {"csrf_token": who.csrf, "password": PASSWORD, "repeat_password": "alpha"}

    dialog = client.post(f"/users/{stand['trainee']['id']}/password", data=form, headers=HTMX)

    assert dialog.status_code == 422
    assert '<span class="field-error">the two passwords differ</span>' in dialog.text
    assert posted(calls) == []


def test_a_weak_password_never_reaches_the_api(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any], calls: list[httpx.Request]
) -> None:
    who = sign_in()
    weak = "Lettersonlyhere"
    form = {"csrf_token": who.csrf, "password": weak, "repeat_password": weak}

    dialog = client.post(f"/users/{stand['trainee']['id']}/password", data=form, headers=HTMX)

    assert dialog.status_code == 422
    assert '<span class="field-error">at least 12 characters and three of' in dialog.text
    assert 'class="field-hint"' not in dialog.text
    assert posted(calls) == []


def test_a_refused_password_shows_the_refusal_next_to_its_field(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any], answer: Answer
) -> None:
    who = sign_in()
    user_id = stand["trainee"]["id"]
    detail = "at least 16 characters"
    answer("POST", f"/api/v1/users/{user_id}/password", {"detail": detail}, 422)
    form = {"csrf_token": who.csrf, "password": PASSWORD, "repeat_password": PASSWORD}

    dialog = client.post(f"/users/{user_id}/password", data=form, headers=HTMX)

    assert dialog.status_code == 422
    assert f'<span class="field-error">{detail}</span>' in dialog.text
    assert f"{detail} (422)" in dialog.text


def test_a_user_is_disabled_through_its_dialog(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any], answer: Answer
) -> None:
    who = sign_in()
    user_id = stand["trainee"]["id"]
    answer("POST", f"/api/v1/users/{user_id}/state", {**stand["trainee"], "state": "disabled"})
    headers = {**HTMX, "hx-current-url": "https://web.example.com/users?role=trainee"}

    dialog = client.get(f"/users/{user_id}/state", headers=headers)
    disabled = client.post(
        f"/users/{user_id}/state",
        data={"csrf_token": who.csrf, "state": "disabled", "next": "/users?role=trainee"},
    )
    shown = client.get("/users")

    assert "beta can no longer log in, and every terminal" in dialog.text
    assert 'name="next" value="/users?role=trainee"' in dialog.text
    assert disabled.headers["location"] == "/users?role=trainee"
    assert "User disabled" in shown.text
    assert "beta can no longer log in." in shown.text


def test_a_refused_state_change_comes_back_as_a_flash(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any], answer: Answer
) -> None:
    who = sign_in()
    user_id = stand["trainee"]["id"]
    detail = "the last operator cannot be disabled"
    answer("POST", f"/api/v1/users/{user_id}/state", {"detail": detail}, 409)

    disabled = client.post(
        f"/users/{user_id}/state", data={"csrf_token": who.csrf, "state": "disabled"}
    )
    shown = client.get("/users")

    assert disabled.headers["location"] == "/users"
    assert f"{detail} (409)" in shown.text
