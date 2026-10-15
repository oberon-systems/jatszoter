import re
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from jatszoter_web.middleware import SECURITY_HEADERS

Routes = dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]]

TASK = "/api/v1/deployments/00000000-0000-4000-8000-0000000000d1/task"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)


@pytest.mark.parametrize(
    "path",
    [
        "/healthz",
        "/login",
        "/",
        "/static/app.css",
        "/static/vendor/htmx-2.0.11/htmx.min.js",
        "/missing",
        "/api/v1/agents",
        TASK,
    ],
)
def test_every_answer_carries_the_security_headers(
    client: TestClient, routes: Routes, path: str
) -> None:
    routes[("GET", TASK)] = lambda _: httpx.Response(
        200, json={}, headers={"referrer-policy": "unsafe-url"}
    )

    response = client.get(path)

    assert response.headers["content-security-policy"] == CSP
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert set(SECURITY_HEADERS) <= set(response.headers)


def test_a_page_loads_nothing_from_another_host_and_runs_no_inline_code(
    client: TestClient,
) -> None:
    page = client.get("/login").text

    assert re.findall(r'(?:src|href)="([^"]+)"', page) == [
        "/static/app.css",
        "/static/vendor/htmx-2.0.11/htmx.min.js",
        "/static/app.js",
    ]
    assert "<style" not in page and "style=" not in page
    assert re.search(r"<script(?![^>]*\bsrc=)", page) is None
    assert "hx-on" not in page


def test_the_stylesheet_names_only_vendored_fonts(client: TestClient) -> None:
    css = client.get("/static/app.css").text

    fonts = re.findall(r'url\("([^"]+)"\)', css)
    assert fonts
    for font in fonts:
        assert font.startswith("vendor/")
        assert client.get(f"/static/{font}").status_code == 200
