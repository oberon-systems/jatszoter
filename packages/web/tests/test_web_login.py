import json
import re
import time
from collections.abc import Callable

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jatszoter_web.cookies import CSRF_COOKIE, SESSION_COOKIE, unseal_operator

Routes = dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]]

LOGIN = ("POST", "/api/v1/auth/login")
ME = ("GET", "/api/v1/auth/me")
LOGOUT = ("POST", "/api/v1/auth/logout")
USER_ID = "00000000-0000-4000-8000-000000000002"
KEY = "issued-key-alpha"
PASSWORD = "Correct horse battery"


def issued(_: httpx.Request) -> httpx.Response:
    expires_at = int(time.time()) + 3600
    credential = {"credential": f"m1:1:{expires_at}", "expires_at": expires_at, "key": KEY}
    return httpx.Response(200, json={"user_id": USER_ID, "credential": credential})


def me(role: str) -> Callable[[httpx.Request], httpx.Response]:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-owner") != USER_ID:
            return httpx.Response(401, json={"detail": "not authorized"})
        return httpx.Response(200, json={"id": USER_ID, "name": "alpha", "role": role})

    return answer


def form(token: str, **fields: str) -> dict[str, str]:
    return {"csrf_token": token, "name": "alpha", "password": PASSWORD, **fields}


def hidden(page: str, name: str) -> str:
    found = re.search(rf'name="{name}" value="([^"]*)"', page)
    assert found is not None
    return found.group(1)


def test_the_login_page_sets_a_csrf_cookie_and_carries_it(client: TestClient) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    token = response.cookies[CSRF_COOKIE]
    assert hidden(response.text, "csrf_token") == token
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=Strict" in cookie
    assert client.get("/login").headers.get("set-cookie") is None


def test_an_operator_logs_in(
    app: FastAPI,
    client: TestClient,
    routes: Routes,
    calls: list[httpx.Request],
    browser_csrf: str,
    signed_by: Callable[[httpx.Request, str], bool],
) -> None:
    routes[LOGIN] = issued
    routes[ME] = me("operator")

    response = client.post("/login", data=form(browser_csrf, next="/agents?state=enabled"))

    assert response.status_code == 303
    assert response.headers["location"] == "/agents?state=enabled"
    assert json.loads(calls[0].content) == {"name": "alpha", "password": PASSWORD}
    assert "x-signature" not in calls[0].headers
    assert signed_by(calls[1], KEY)
    sealed = response.cookies[SESSION_COOKIE]
    operator = unseal_operator(sealed, app.state.keys)
    assert operator is not None
    assert (operator.name, str(operator.user_id), operator.key) == ("alpha", USER_ID, KEY)
    assert KEY not in sealed and KEY not in response.text
    assert operator.csrf != browser_csrf


@pytest.mark.parametrize("next_page", ["https://beta.example.com/", "//beta.example.com", "x"])
def test_a_foreign_next_page_is_dropped(
    client: TestClient, routes: Routes, browser_csrf: str, next_page: str
) -> None:
    routes[LOGIN] = issued
    routes[ME] = me("operator")

    response = client.post("/login", data=form(browser_csrf, next=next_page))

    assert response.headers["location"] == "/deployments"


@pytest.mark.parametrize(
    ("status", "text"),
    [(401, "wrong name or password (401)"), (429, "too many attempts, try again later (429)")],
)
def test_a_refused_login_says_why(
    client: TestClient, routes: Routes, browser_csrf: str, status: int, text: str
) -> None:
    routes[LOGIN] = lambda _: httpx.Response(status, json={"detail": "not authorized"})

    response = client.post("/login", data=form(browser_csrf))

    assert response.status_code == status
    assert text in response.text
    assert SESSION_COOKIE not in response.cookies
    assert PASSWORD not in response.text


def test_a_user_who_is_not_an_operator_is_logged_out(
    client: TestClient, routes: Routes, calls: list[httpx.Request], browser_csrf: str
) -> None:
    routes[LOGIN] = issued
    routes[ME] = me("trainee")
    routes[LOGOUT] = lambda _: httpx.Response(204)

    response = client.post("/login", data=form(browser_csrf))

    assert response.status_code == 403
    assert "This account is not an operator&#39;s and was logged out." in response.text
    assert [f"{call.method} {call.url.path}" for call in calls][-1] == "POST /api/v1/auth/logout"
    assert SESSION_COOKIE not in response.cookies


def test_a_first_login_sets_a_new_password(
    client: TestClient, routes: Routes, calls: list[httpx.Request], browser_csrf: str
) -> None:
    def login(request: httpx.Request) -> httpx.Response:
        if "new_password" not in json.loads(request.content):
            return httpx.Response(403, json={"detail": "password change required"})
        return issued(request)

    routes[LOGIN] = login
    routes[ME] = me("operator")

    asked = client.post("/login", data=form(browser_csrf, next="/users"))

    assert asked.status_code == 200
    assert "Set a new password" in asked.text
    assert PASSWORD not in asked.text
    pending = hidden(asked.text, "pending")
    change = {
        "csrf_token": browser_csrf,
        "pending": pending,
        "next": "/users",
        "new_password": "Staple of the paddock",
    }

    differ = client.post("/login", data={**change, "repeat_password": "Staple of the stable"})

    assert differ.status_code == 422
    assert "the two passwords differ" in differ.text
    assert len(calls) == 1

    changed = client.post("/login", data={**change, "repeat_password": "Staple of the paddock"})

    assert changed.status_code == 303
    assert changed.headers["location"] == "/users"
    assert json.loads(calls[1].content) == {
        "name": "alpha",
        "password": PASSWORD,
        "new_password": "Staple of the paddock",
    }
    assert SESSION_COOKIE in changed.cookies


def test_a_refused_new_password_shows_the_api_message(
    client: TestClient, routes: Routes, browser_csrf: str
) -> None:
    def login(request: httpx.Request) -> httpx.Response:
        if "new_password" not in json.loads(request.content):
            return httpx.Response(403, json={"detail": "password change required"})
        return httpx.Response(422, json={"detail": "the new password is too short"})

    routes[LOGIN] = login
    pending = hidden(client.post("/login", data=form(browser_csrf)).text, "pending")

    response = client.post(
        "/login",
        data={
            "csrf_token": browser_csrf,
            "pending": pending,
            "new_password": "Staple of the paddock",
            "repeat_password": "Staple of the paddock",
        },
    )

    assert response.status_code == 422
    assert "the new password is too short (422)" in response.text
    assert hidden(response.text, "pending") == pending


@pytest.mark.parametrize(
    ("new_password", "repeat", "shown"),
    [
        ("short", "short", "at least 12 characters and three of"),
        ("onlylowercaseletters", "onlylowercaseletters", "at least 12 characters and three of"),
        (PASSWORD, PASSWORD, "must differ from the current password"),
        ("Staple of the paddock", "Staple of the stable", "the two passwords differ"),
    ],
)
def test_a_new_password_that_breaks_the_rule_never_reaches_the_api(
    client: TestClient,
    routes: Routes,
    calls: list[httpx.Request],
    browser_csrf: str,
    new_password: str,
    repeat: str,
    shown: str,
) -> None:
    routes[LOGIN] = lambda _: httpx.Response(403, json={"detail": "password change required"})
    asked = client.post("/login", data=form(browser_csrf))
    change = {
        "csrf_token": browser_csrf,
        "pending": hidden(asked.text, "pending"),
        "new_password": new_password,
        "repeat_password": repeat,
    }

    response = client.post("/login", data=change)

    assert 'class="field-hint">at least 12 characters' in asked.text
    assert response.status_code == 422
    assert f'<span class="field-error">{shown}' in response.text
    assert len(calls) == 1


def test_a_forged_pending_login_starts_over(
    client: TestClient, calls: list[httpx.Request], browser_csrf: str
) -> None:
    response = client.post(
        "/login",
        data={
            "csrf_token": browser_csrf,
            "pending": "k1.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "new_password": "Staple of the paddock",
            "repeat_password": "Staple of the paddock",
        },
    )

    assert response.status_code == 200
    assert 'name="password"' in response.text
    assert calls == []


@pytest.mark.parametrize(
    ("token", "headers"),
    [
        (None, {}),
        ("csrf-token-other", {}),
        ("csrf-token-browser", {"origin": "https://beta.example.com"}),
    ],
)
def test_a_login_without_the_csrf_token_is_refused(
    client: TestClient,
    calls: list[httpx.Request],
    browser_csrf: str,
    token: str | None,
    headers: dict[str, str],
) -> None:
    data = form(token or "")
    if token is None:
        del data["csrf_token"]

    response = client.post("/login", data=data, headers=headers)

    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    ("failure", "status", "text"),
    [
        (httpx.ConnectError("refused"), 502, "The API cannot be reached."),
        (httpx.ReadTimeout("slow"), 504, "The API did not answer in time."),
    ],
)
def test_an_api_that_is_down_shows_the_error_state(
    client: TestClient,
    routes: Routes,
    browser_csrf: str,
    failure: Exception,
    status: int,
    text: str,
) -> None:
    def down(_: httpx.Request) -> httpx.Response:
        raise failure

    routes[LOGIN] = down

    response = client.post("/login", data=form(browser_csrf))

    assert response.status_code == status
    assert text in response.text
    assert "api.example.com" not in response.text
    assert "Traceback" not in response.text
