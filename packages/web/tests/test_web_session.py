import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jatszoter_web.cookies import SESSION_COOKIE, Operator, unseal_operator
from jatszoter_web.session import OperatorDep, PostedOperatorDep

Routes = dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]]
SignIn = Callable[..., Operator]

ME = ("GET", "/api/v1/auth/me")
REFRESH = ("POST", "/api/v1/auth/credential")
LOGOUT = ("POST", "/api/v1/auth/logout")
HTMX = {"hx-request": "true", "hx-current-url": "https://web.example.com/agents?state=enabled"}


@pytest.fixture
def probed(app: FastAPI, routes: Routes) -> FastAPI:
    """Routes standing in for the screens: one signed read, one signed write."""

    @app.get("/probe")
    async def probe(session: OperatorDep) -> Any:
        return (await session.call("GET", "/api/v1/auth/me")).json()

    @app.post("/probe")
    async def post_probe(session: PostedOperatorDep) -> Any:
        return (await session.call("GET", "/api/v1/auth/me")).json()

    routes[ME] = lambda _: httpx.Response(200, json={"name": "alpha"})
    return app


def cleared(response: httpx.Response) -> bool:
    return any(
        value.startswith(f"{SESSION_COOKIE}=;") and "Max-Age=0" in value
        for value in response.headers.get_list("set-cookie")
    )


@pytest.mark.usefixtures("probed")
def test_a_signed_call_carries_the_session_credential(
    client: TestClient,
    sign_in: SignIn,
    calls: list[httpx.Request],
    signed_by: Callable[[httpx.Request, str], bool],
) -> None:
    who = sign_in()

    response = client.get("/probe")

    assert response.status_code == 200
    assert response.json() == {"name": "alpha"}
    assert calls[0].headers["x-owner"] == str(who.user_id)
    assert calls[0].headers["x-credential"] == who.credential
    assert signed_by(calls[0], who.key)
    assert "cookie" not in calls[0].headers
    assert who.key not in response.text


@pytest.mark.usefixtures("probed")
def test_no_session_lands_on_the_login_page(client: TestClient, calls: list[httpx.Request]) -> None:
    page = client.get("/probe?view=all")
    fragment = client.get("/probe", headers=HTMX)

    assert page.status_code == 303
    assert page.headers["location"] == "/login?next=/probe%3Fview%3Dall"
    assert fragment.headers["hx-redirect"] == "/login?next=/agents%3Fstate%3Denabled"
    assert calls == []


@pytest.mark.usefixtures("probed")
@pytest.mark.parametrize("value", ["k1.AAAA", "k9.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "x"])
def test_a_cookie_that_does_not_decrypt_is_a_logged_out_browser(
    client: TestClient, calls: list[httpx.Request], value: str
) -> None:
    client.cookies.set(SESSION_COOKIE, value, domain="web.example.com")

    response = client.get("/probe")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert cleared(response)
    assert calls == []


@pytest.mark.usefixtures("probed")
def test_a_tampered_cookie_is_a_logged_out_browser(
    client: TestClient, sign_in: SignIn, calls: list[httpx.Request]
) -> None:
    sign_in()
    sealed = client.cookies[SESSION_COOKIE]
    client.cookies.set(SESSION_COOKIE, sealed[:-3] + "AAA", domain="web.example.com")

    response = client.get("/probe")

    assert response.status_code == 303
    assert cleared(response)
    assert calls == []


@pytest.mark.usefixtures("probed")
def test_a_lapsed_credential_is_never_sent(
    client: TestClient, sign_in: SignIn, calls: list[httpx.Request]
) -> None:
    sign_in(expires_in=-1)

    page = client.get("/probe")
    sign_in(expires_in=-1)
    fragment = client.get("/probe", headers=HTMX)

    assert page.status_code == 303
    assert page.headers["location"] == "/login?next=/probe"
    assert cleared(page)
    assert "hx-redirect" in fragment.headers
    assert calls == []


@pytest.mark.usefixtures("probed")
def test_a_credential_due_for_refresh_is_refreshed_first(
    app: FastAPI,
    client: TestClient,
    sign_in: SignIn,
    routes: Routes,
    calls: list[httpx.Request],
    signed_by: Callable[[httpx.Request, str], bool],
) -> None:
    who = sign_in(expires_in=10 * 60)
    expires_at = int(time.time()) + 3600
    fresh = {"credential": f"m1:2:{expires_at}", "expires_at": expires_at, "key": "fresh-key"}
    routes[REFRESH] = lambda _: httpx.Response(200, json=fresh)

    response = client.get("/probe")

    assert response.status_code == 200
    assert [call.url.path for call in calls] == ["/api/v1/auth/credential", "/api/v1/auth/me"]
    assert signed_by(calls[0], who.key)
    assert calls[1].headers["x-credential"] == fresh["credential"]
    assert signed_by(calls[1], "fresh-key")
    renewed = unseal_operator(response.cookies[SESSION_COOKIE], app.state.keys)
    assert renewed is not None
    assert (renewed.key, renewed.expires_at, renewed.csrf) == ("fresh-key", expires_at, who.csrf)
    assert "fresh-key" not in response.text


@pytest.mark.usefixtures("probed")
def test_a_credential_not_yet_due_is_not_refreshed(
    client: TestClient, sign_in: SignIn, calls: list[httpx.Request]
) -> None:
    sign_in(expires_in=16 * 60)

    response = client.get("/probe")

    assert [call.url.path for call in calls] == ["/api/v1/auth/me"]
    assert SESSION_COOKIE not in response.cookies


@pytest.mark.usefixtures("probed")
def test_an_api_refusal_signs_the_browser_out(
    client: TestClient, sign_in: SignIn, routes: Routes
) -> None:
    routes[ME] = lambda _: httpx.Response(401, json={"detail": "not authorized"})
    sign_in()

    fragment = client.get("/probe", headers=HTMX)
    page = client.get("/probe")

    assert fragment.headers["hx-refresh"] == "true"
    assert not cleared(fragment)
    assert page.status_code == 303
    assert page.headers["location"] == "/login?next=/probe"
    assert cleared(page)


@pytest.mark.usefixtures("probed")
def test_a_forbidden_call_shows_the_not_allowed_page(
    client: TestClient, sign_in: SignIn, routes: Routes
) -> None:
    routes[ME] = lambda _: httpx.Response(403, json={"detail": "forbidden"})
    sign_in()

    response = client.get("/probe")

    assert response.status_code == 403
    assert "This action is not allowed for your account. (403)" in response.text
    assert ">alpha<" in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.usefixtures("probed")
@pytest.mark.parametrize(
    ("answer", "status", "text"),
    [
        (httpx.ConnectError("refused"), 502, "The API cannot be reached. Nothing was changed"),
        (httpx.ReadTimeout("slow"), 504, "The API did not answer in time."),
        (httpx.Response(500, text="Traceback: boom"), 502, "The API failed to answer."),
    ],
)
def test_an_api_that_is_down_shows_the_error_state(
    client: TestClient,
    sign_in: SignIn,
    routes: Routes,
    answer: Exception | httpx.Response,
    status: int,
    text: str,
) -> None:
    def down(_: httpx.Request) -> httpx.Response:
        if isinstance(answer, Exception):
            raise answer
        return answer

    routes[ME] = down
    sign_in()

    response = client.get("/probe")

    assert response.status_code == status
    assert text in response.text
    assert "api.example.com" not in response.text
    assert "boom" not in response.text


@pytest.mark.usefixtures("probed")
@pytest.mark.parametrize(
    ("data", "headers"),
    [
        ({}, {}),
        ({"csrf_token": "csrf-token-other"}, {}),
        ({}, {"x-csrf-token": "csrf-token-other"}),
        ({"csrf_token": "csrf-token-alpha"}, {"origin": "https://beta.example.com"}),
    ],
)
def test_a_post_without_the_session_csrf_token_changes_nothing(
    client: TestClient,
    sign_in: SignIn,
    calls: list[httpx.Request],
    data: dict[str, str],
    headers: dict[str, str],
) -> None:
    sign_in()

    response = client.post("/probe", data=data, headers=headers)

    assert response.status_code == 403
    assert calls == []


@pytest.mark.usefixtures("probed")
def test_a_post_with_the_session_csrf_token_goes_through(
    client: TestClient, sign_in: SignIn, calls: list[httpx.Request]
) -> None:
    who = sign_in()

    by_form = client.post("/probe", data={"csrf_token": who.csrf})
    by_header = client.post(
        "/probe",
        headers={"x-csrf-token": who.csrf, "origin": "https://web.example.com"},
    )

    assert (by_form.status_code, by_header.status_code) == (200, 200)
    assert len(calls) == 2


def test_the_root_sends_an_operator_to_the_deployments(client: TestClient, sign_in: SignIn) -> None:
    assert client.get("/").headers["location"] == "/login"

    sign_in()

    assert client.get("/").headers["location"] == "/deployments"


def test_the_shell_shows_the_navigation_and_the_operator(
    client: TestClient, sign_in: SignIn, routes: Routes, app: FastAPI
) -> None:
    @app.get("/shell")
    async def shell(session: OperatorDep) -> Any:
        await session.call("GET", "/api/v1/auth/me")

    routes[ME] = lambda _: httpx.Response(403, json={"detail": "forbidden"})
    who = sign_in()

    page = client.get("/shell").text

    for label in ("Deployments", "Scenarios", "Agents", "Users", "Logs", "Storage", "Results"):
        assert f">{label}</a>" in page
    assert f'<meta name="csrf-token" content="{who.csrf}">' in page
    assert f'hx-headers=\'{{"X-CSRF-Token": "{who.csrf}"}}\'' in page
    assert 'action="/logout"' in page
    assert who.key not in page and who.credential not in page


def test_logout_ends_the_session_at_the_api(
    client: TestClient,
    sign_in: SignIn,
    routes: Routes,
    calls: list[httpx.Request],
    signed_by: Callable[[httpx.Request, str], bool],
) -> None:
    routes[LOGOUT] = lambda _: httpx.Response(204)
    who = sign_in()

    response = client.post("/logout", data={"csrf_token": who.csrf})

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert cleared(response)
    assert [call.url.path for call in calls] == ["/api/v1/auth/logout"]
    assert signed_by(calls[0], who.key)


@pytest.mark.parametrize(
    "answer", [httpx.Response(401, json={"detail": "not authorized"}), httpx.Response(500)]
)
def test_logout_clears_the_cookie_whatever_the_api_answers(
    client: TestClient, sign_in: SignIn, routes: Routes, answer: httpx.Response
) -> None:
    routes[LOGOUT] = lambda _: answer
    who = sign_in()

    response = client.post("/logout", data={"csrf_token": who.csrf})

    assert response.status_code == 303
    assert cleared(response)


def test_logout_with_an_api_that_is_down_clears_the_cookie(
    client: TestClient, sign_in: SignIn, routes: Routes
) -> None:
    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    routes[LOGOUT] = down
    who = sign_in()

    response = client.post("/logout", data={"csrf_token": who.csrf})

    assert response.status_code == 303
    assert cleared(response)


def test_logout_without_the_csrf_token_changes_nothing(
    client: TestClient, sign_in: SignIn, calls: list[httpx.Request]
) -> None:
    sign_in()

    response = client.post("/logout")

    assert response.status_code == 403
    assert not cleared(response)
    assert calls == []
