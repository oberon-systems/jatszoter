from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jatszoter_web.cookies import CSRF_COOKIE, Operator
from jatszoter_web.session import PostedOperatorDep

Routes = dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]]
SignIn = Callable[..., Operator]

HTMX = {"hx-request": "true", "hx-current-url": "https://web.example.com/agents"}
EXPIRED = "The form has expired or came from another page."
TOKEN = "tok-alpha"


@pytest.fixture
def probed(app: FastAPI, routes: Routes) -> FastAPI:
    """A signed write, a typed read and a route that fails, standing in for the screens."""

    @app.post("/probe")
    async def post_probe(session: PostedOperatorDep) -> Any:
        return (await session.call("GET", "/api/v1/auth/me")).json()

    @app.get("/typed")
    def typed(count: int) -> dict[str, int]:
        return {"count": count}

    @app.get("/broken")
    def broken() -> None:
        raise RuntimeError("broken")

    routes[("GET", "/api/v1/auth/me")] = lambda _: httpx.Response(200, json={"name": "alpha"})
    return app


def html(response: httpx.Response) -> bool:
    return response.headers["content-type"].startswith("text/html")


def test_an_unknown_page_shows_not_found_in_the_operator_shell(
    client: TestClient, sign_in: SignIn
) -> None:
    sign_in()

    response = client.get("/nowhere")

    assert response.status_code == 404
    assert html(response)
    assert "Page not found" in response.text
    assert "Check the link or start from the deployments. (404)" in response.text
    assert ">alpha<" in response.text
    assert 'href="/deployments">Go to deployments' in response.text


def test_an_unknown_page_shows_not_found_to_a_signed_out_browser(client: TestClient) -> None:
    response = client.get("/nowhere")

    assert response.status_code == 404
    assert html(response)
    assert "Check the link or log in. (404)" in response.text
    assert 'href="/login">Log in' in response.text
    assert "Go to deployments" not in response.text


def test_a_path_under_the_api_keeps_its_json(client: TestClient) -> None:
    response = client.get("/api/v1/agents")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_a_wrong_method_shows_the_refusal(client: TestClient) -> None:
    response = client.put("/login")

    assert response.status_code == 405
    assert html(response)
    assert "This request is not accepted: method not allowed." in response.text


@pytest.mark.usefixtures("probed")
def test_an_invalid_request_shows_the_refusal(client: TestClient, sign_in: SignIn) -> None:
    sign_in()

    response = client.get("/typed", params={"count": "many"})

    assert response.status_code == 422
    assert html(response)
    assert "This request is not accepted: the request is not valid." in response.text


@pytest.mark.usefixtures("probed")
def test_a_refused_csrf_token_shows_the_expired_form_page(
    client: TestClient, sign_in: SignIn, calls: list[httpx.Request]
) -> None:
    sign_in()

    response = client.post(
        "/probe", data={"csrf_token": "other"}, headers={"referer": "https://web.example.com/users"}
    )

    assert response.status_code == 403
    assert html(response)
    assert EXPIRED in response.text
    assert 'href="/users">Reload page' in response.text
    assert ">alpha<" in response.text
    assert calls == []


@pytest.mark.usefixtures("probed")
def test_a_foreign_referer_gets_no_reload_link(client: TestClient, sign_in: SignIn) -> None:
    sign_in()

    response = client.post(
        "/probe", data={"csrf_token": "other"}, headers={"referer": "https://beta.example.com/"}
    )

    assert response.status_code == 403
    assert "Reload page" not in response.text


@pytest.mark.usefixtures("probed")
def test_a_refused_csrf_token_from_htmx_fills_the_flash_slot(
    client: TestClient, sign_in: SignIn
) -> None:
    sign_in()

    response = client.post("/probe", headers=HTMX)

    assert response.status_code == 403
    assert response.headers["hx-retarget"] == "#flashes"
    assert EXPIRED in response.text


def test_a_login_with_an_expired_form_shows_the_form_again(
    client: TestClient, calls: list[httpx.Request]
) -> None:
    response = client.post(
        "/login", data={"csrf_token": "stale", "name": "alpha", "password": "x", "next": "/users"}
    )

    assert response.status_code == 403
    assert html(response)
    assert "The login form has expired. Nothing was sent;" in response.text
    assert 'value="alpha"' in response.text
    assert 'name="next" value="/users"' in response.text
    assert response.cookies[CSRF_COOKIE]
    assert calls == []


@pytest.mark.usefixtures("probed")
def test_a_failing_page_shows_the_server_error_page(
    app: FastAPI, client: TestClient, sign_in: SignIn
) -> None:
    sign_in()
    quiet = TestClient(
        app,
        base_url=str(client.base_url),
        raise_server_exceptions=False,
        cookies=client.cookies,
    )

    response = quiet.get("/broken")

    assert response.status_code == 500
    assert html(response)
    assert "The page failed on the server." in response.text
    assert 'href="/broken">Reload page' in response.text
    assert ">alpha<" in response.text
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("token", [".alpha", "al~pha"])
def test_a_malformed_link_shows_the_link_ended_card(
    client: TestClient, calls: list[httpx.Request], token: str
) -> None:
    response = client.get(f"/s/{token}")

    assert response.status_code == 404
    assert "this link is not valid or has ended" in response.text
    assert calls == []


@pytest.mark.parametrize("status", [404, 410])
def test_a_refused_link_shows_the_link_ended_card(
    client: TestClient, routes: Routes, status: int
) -> None:
    routes[("GET", f"/s/{TOKEN}")] = lambda _: httpx.Response(status, json={"detail": "gone"})

    response = client.get(f"/s/{TOKEN}")

    assert response.status_code == status
    assert html(response)
    assert "this link is not valid or has ended" in response.text
    assert "gone" not in response.text


def test_a_failing_api_on_a_link_shows_the_error_page(client: TestClient, routes: Routes) -> None:
    routes[("GET", f"/s/{TOKEN}")] = lambda _: httpx.Response(500)

    response = client.get(f"/s/{TOKEN}")

    assert response.status_code == 502
    assert html(response)
    assert "The API failed to answer. Try again in a moment. (500)" in response.text
