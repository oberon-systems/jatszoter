import logging
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from jatszoter_web.cookies import CSRF_COOKIE, LINK_COOKIE, SESSION_COOKIE, Operator

Routes = dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]]

DEPLOYMENT = "00000000-0000-4000-8000-0000000000d1"
TASK = f"/api/v1/deployments/{DEPLOYMENT}/task"
CHECKS = f"/api/v1/deployments/{DEPLOYMENT}/checks"
TOKEN = "link-token-alpha"
HOST = "web.example.com"
ORIGIN = f"https://{HOST}"


@pytest.fixture
def linked(client: TestClient) -> TestClient:
    client.cookies.set(LINK_COOKIE, "link-session-alpha", domain=HOST)
    return client


def test_a_link_open_is_relayed_and_redacted_in_the_log(
    client: TestClient,
    routes: Routes,
    calls: list[httpx.Request],
    caplog: pytest.LogCaptureFixture,
) -> None:
    link = f"{LINK_COOKIE}=s1; HttpOnly; Path=/api/v1/deployments/{DEPLOYMENT}; SameSite=strict"
    routes[("GET", f"/s/{TOKEN}")] = lambda _: httpx.Response(
        303,
        headers=[
            ("location", f"/t/{DEPLOYMENT}"),
            ("set-cookie", link),
            ("referrer-policy", "no-referrer"),
            ("x-internal", "kept back"),
        ],
    )
    caplog.set_level(logging.INFO, logger="jatszoter_web.access")

    response = client.get(f"/s/{TOKEN}")

    assert response.status_code == 303
    assert response.headers["location"] == f"/t/{DEPLOYMENT}"
    assert response.headers.get_list("set-cookie") == [link]
    assert "x-internal" not in response.headers
    assert calls[0].headers["host"] == HOST
    assert "GET /s/<redacted> 303" in caplog.text
    assert TOKEN not in caplog.text


def test_a_link_call_passes_the_allow_listed_headers_alone(
    linked: TestClient, sign_in: Callable[..., Operator], routes: Routes, calls: list[httpx.Request]
) -> None:
    routes[("GET", TASK)] = lambda _: httpx.Response(
        200, json={"status": "READY"}, headers={"cache-control": "no-store"}
    )
    sign_in()
    linked.cookies.set(CSRF_COOKIE, "csrf-token-browser", domain=HOST)

    response = linked.get(
        f"{TASK}?offset=12&x=a%20b",
        headers={
            "origin": ORIGIN,
            "accept": "application/json",
            "x-owner": "00000000-0000-4000-8000-000000000001",
            "x-credential": "m1:1:1",
            "x-timestamp": "1",
            "x-signature": "forged",
            "authorization": "Basic YWxwaGE6YmV0YQ==",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "READY"}
    assert response.headers["cache-control"] == "no-store"
    sent = calls[0]
    assert sent.url.path == TASK
    assert sent.url.query == b"offset=12&x=a%20b"
    assert sent.headers["host"] == HOST
    assert sent.headers["origin"] == ORIGIN
    assert sent.headers["cookie"] == f"{LINK_COOKIE}=link-session-alpha"
    for name in ("x-owner", "x-credential", "x-timestamp", "x-signature", "authorization"):
        assert name not in sent.headers
    assert SESSION_COOKIE not in sent.headers["cookie"]
    assert CSRF_COOKIE not in sent.headers["cookie"]


def test_a_link_post_with_the_csrf_token_is_relayed(
    linked: TestClient, browser_csrf: str, routes: Routes, calls: list[httpx.Request]
) -> None:
    routes[("POST", CHECKS)] = lambda request: httpx.Response(202, content=request.content)

    response = linked.post(
        CHECKS,
        content=b'{"note": "alpha"}',
        headers={"content-type": "application/json", "x-csrf-token": browser_csrf},
    )

    assert response.status_code == 202
    assert response.content == b'{"note": "alpha"}'
    assert calls[0].headers["content-type"] == "application/json"
    assert "x-csrf-token" not in calls[0].headers


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"x-csrf-token": "csrf-token-other"},
        {"x-csrf-token": "csrf-token-browser", "origin": "https://beta.example.com"},
    ],
)
def test_a_link_post_without_the_csrf_token_reaches_no_api_route(
    linked: TestClient, browser_csrf: str, calls: list[httpx.Request], headers: dict[str, str]
) -> None:
    response = linked.post(CHECKS, json={}, headers=headers)

    assert response.status_code == 403
    assert calls == []


def test_a_link_post_in_a_form_field_is_not_enough(
    linked: TestClient, browser_csrf: str, calls: list[httpx.Request]
) -> None:
    response = linked.post(CHECKS, data={"csrf_token": browser_csrf})

    assert response.status_code == 403
    assert calls == []


def test_a_body_over_64_kib_is_refused(
    linked: TestClient, browser_csrf: str, calls: list[httpx.Request]
) -> None:
    response = linked.post(
        CHECKS, content=b"a" * (64 * 1024 + 1), headers={"x-csrf-token": browser_csrf}
    )

    assert response.status_code == 413
    assert calls == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/agents",
        "/api/v1/users",
        f"/api/v1/deployments/{DEPLOYMENT}",
        f"/api/v1/deployments/{DEPLOYMENT}/",
        "/api/v1/deployments/alpha/task",
        f"/api/v1/deployments/{DEPLOYMENT}/%2E%2E/%2E%2E/agents",
        f"/api/v1/deployments/{DEPLOYMENT}/.hidden",
        "/api/v1/recordings/alpha.cast",
    ],
)
def test_a_path_outside_the_relay_is_404_from_web(
    linked: TestClient, calls: list[httpx.Request], path: str
) -> None:
    assert linked.get(path).status_code == 404
    assert calls == []


@pytest.mark.parametrize(
    ("failure", "status"),
    [(httpx.ConnectError("refused"), 502), (httpx.ReadTimeout("slow"), 504)],
)
def test_an_api_that_is_down_answers_the_relay_with_a_gateway_status(
    linked: TestClient, routes: Routes, failure: Exception, status: int
) -> None:
    def down(_: httpx.Request) -> httpx.Response:
        raise failure

    routes[("GET", TASK)] = down

    response = linked.get(TASK)

    assert response.status_code == status
    assert "api.example.com" not in response.text
