import logging
import re
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from jatszoter_web.cookies import CSRF_COOKIE, Operator
from jatszoter_web.middleware import Guard
from jatszoter_web.session import OperatorDep, PostedOperatorDep

Routes = dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]]
SignIn = Callable[..., Operator]

HTMX = {"hx-request": "true", "hx-current-url": "https://web.example.com/agents"}
EXPIRED = "The form has expired or came from another page."
TOKEN = "tok-alpha"
ANSWERS = "jatszoter_web.answers"
ERROR_ID = "1b4e28ba-2fa1-41d2-883f-0016d3cca427"


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
    @app.get("/api/broken")
    def broken() -> None:
        raise RuntimeError("broken")

    @app.get("/unavailable")
    def unavailable() -> None:
        raise HTTPException(status_code=503, detail="alpha detail")

    @app.get("/me-probe")
    async def me_probe(session: OperatorDep) -> Any:
        return (await session.call("GET", "/api/v1/auth/me")).json()

    routes[("GET", "/api/v1/auth/me")] = lambda _: httpx.Response(200, json={"name": "alpha"})
    return app


def html(response: httpx.Response) -> bool:
    return response.headers["content-type"].startswith("text/html")


def shown_id(response: httpx.Response, status: int) -> str:
    """The error id the page shows, in the form every failure page writes it."""
    found = re.search(rf"\(error ([0-9a-f-]{{36}})\) \({status}\)", response.text)
    assert found is not None
    return str(uuid.UUID(found.group(1)))


async def raising(_: Scope, __: Receive, ___: Send) -> None:
    raise RuntimeError("alpha")


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


def test_a_path_under_the_api_answers_a_code_and_an_id(client: TestClient) -> None:
    response = client.get("/api/v1/agents")

    assert response.status_code == 404
    assert set(response.json()) == {"error", "error_id"}
    assert response.json()["error"] == "not-found"


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
    body = {"error": "gone", "error_id": "1b4e28ba-2fa1-41d2-883f-0016d3cca427"}
    routes[("GET", f"/s/{TOKEN}")] = lambda _: httpx.Response(status, json=body)

    response = client.get(f"/s/{TOKEN}")

    assert response.status_code == status
    assert html(response)
    assert "this link is not valid or has ended" in response.text
    assert "gone" not in response.text


def test_a_failing_api_on_a_link_shows_the_error_page(
    client: TestClient, routes: Routes, caplog: pytest.LogCaptureFixture
) -> None:
    routes[("GET", f"/s/{TOKEN}")] = lambda _: httpx.Response(500)
    caplog.set_level(logging.ERROR, logger=ANSWERS)

    response = client.get(f"/s/{TOKEN}")

    assert response.status_code == 502
    assert html(response)
    assert "The API failed to answer. Try again in a moment. (error " in response.text
    assert shown_id(response, 500) in caplog.text
    assert TOKEN not in caplog.text


@pytest.mark.usefixtures("probed")
def test_a_failing_page_logs_the_id_it_shows(
    client: TestClient, sign_in: SignIn, caplog: pytest.LogCaptureFixture
) -> None:
    sign_in()
    caplog.set_level(logging.ERROR, logger=ANSWERS)

    response = client.get("/broken")

    assert response.status_code == 500
    [record] = [record for record in caplog.records if record.name == ANSWERS]
    assert shown_id(response, 500) in record.getMessage()
    assert "RuntimeError: broken" in caplog.text
    assert "RuntimeError" not in response.text


@pytest.mark.usefixtures("probed")
def test_a_failing_page_from_htmx_shows_the_id_in_the_flash_slot(
    client: TestClient, sign_in: SignIn, caplog: pytest.LogCaptureFixture
) -> None:
    sign_in()
    caplog.set_level(logging.ERROR, logger=ANSWERS)

    response = client.get("/broken", headers=HTMX)

    assert response.status_code == 500
    assert response.headers["hx-retarget"] == "#flashes"
    assert shown_id(response, 500) in caplog.text


@pytest.mark.usefixtures("probed")
def test_a_failure_under_the_api_answers_the_logged_id(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger=ANSWERS)

    response = client.get("/api/broken")

    assert response.status_code == 500
    assert set(response.json()) == {"error", "error_id"}
    assert response.json()["error"] == "internal-error"
    assert response.json()["error_id"] in caplog.text


@pytest.mark.usefixtures("probed")
def test_an_http_failure_of_a_page_logs_the_id_it_shows(
    client: TestClient, sign_in: SignIn, caplog: pytest.LogCaptureFixture
) -> None:
    sign_in()
    caplog.set_level(logging.ERROR, logger=ANSWERS)

    response = client.get("/unavailable")

    assert response.status_code == 500
    assert shown_id(response, 500) in caplog.text
    assert "alpha detail" in caplog.text
    assert "alpha detail" not in response.text


@pytest.mark.usefixtures("probed")
@pytest.mark.parametrize(
    ("answer", "status"),
    [(httpx.ConnectError("beta"), 502), (httpx.ReadTimeout("beta"), 504)],
)
def test_an_api_out_of_reach_logs_the_id_the_page_shows(
    client: TestClient,
    sign_in: SignIn,
    routes: Routes,
    caplog: pytest.LogCaptureFixture,
    answer: Exception,
    status: int,
) -> None:
    def down(_: httpx.Request) -> httpx.Response:
        raise answer

    sign_in()
    routes[("GET", "/api/v1/auth/me")] = down
    caplog.set_level(logging.ERROR, logger=ANSWERS)

    response = client.get("/me-probe")

    assert response.status_code == status
    assert shown_id(response, status) in caplog.text
    assert "beta" in caplog.text
    assert "beta" not in response.text


@pytest.mark.usefixtures("probed")
def test_a_failing_api_shows_the_id_the_api_logged(
    client: TestClient, sign_in: SignIn, routes: Routes, caplog: pytest.LogCaptureFixture
) -> None:
    sign_in()
    body = {"error": "internal-error", "error_id": ERROR_ID}
    routes[("GET", "/api/v1/auth/me")] = lambda _: httpx.Response(500, json=body)
    caplog.set_level(logging.ERROR, logger=ANSWERS)

    response = client.get("/me-probe")

    assert response.status_code == 502
    assert shown_id(response, 500) == ERROR_ID
    assert ERROR_ID not in caplog.text


def test_a_failing_error_page_answers_the_id_in_plain_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def no_page(_: Scope, __: str) -> Response:
        raise RuntimeError("no page")

    caplog.set_level(logging.ERROR, logger=ANSWERS)
    guarded = TestClient(Guard(raising, failure=no_page))

    response = guarded.get("/")

    assert response.status_code == 500
    assert response.text.startswith("Internal Server Error (error ")
    assert response.text.removesuffix(")").rsplit(" ", 1)[1] in caplog.text


def test_a_failure_after_the_answer_started_is_raised_again() -> None:
    async def started(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await raising(scope, receive, send)

    with pytest.raises(RuntimeError, match="alpha"):
        TestClient(Guard(started)).get("/")
