from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from jatszoter_web.cookies import Operator

Answer = Callable[..., None]
Routes = dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]]
SignIn = Callable[..., Operator]


def test_each_deployment_offers_its_log_or_says_why_not(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any], answer: Answer
) -> None:
    sign_in()
    held = stand["deployment"]
    purged = {**held, "id": "00000000-0000-4000-8000-0000000000d2", "status": "COMPLETED"}
    waiting = {**held, "id": "00000000-0000-4000-8000-0000000000d3", "status": "PENDING"}
    answer("GET", "/api/v1/deployments", [held, purged, {**waiting, "vm_id": None}])

    page = client.get("/logs")

    assert page.status_code == 200
    assert f'href="/deployments/{held["id"]}/console.log"' in page.text
    assert page.text.count("console.log") == 1
    assert '<span class="caption">purged</span>' in page.text
    assert '<span class="caption">no log yet</span>' in page.text


@pytest.mark.usefixtures("stand")
def test_no_deployments_shows_the_empty_board(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    sign_in()
    answer("GET", "/api/v1/deployments", [])

    assert "No deployments yet" in client.get("/logs").text


@pytest.mark.usefixtures("stand")
def test_a_refused_list_shows_the_refusal(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    sign_in()
    answer("GET", "/api/v1/storage", {"detail": "storage is not listed"}, 422)

    page = client.get("/logs")

    assert page.status_code == 422
    assert "storage is not listed (422)" in page.text


def test_a_console_log_downloads_as_a_file(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any], routes: Routes
) -> None:
    sign_in()
    deployment_id = stand["deployment"]["id"]
    console = f"/api/v1/deployments/{deployment_id}/console"
    routes[("GET", console)] = lambda _: httpx.Response(200, content=b"boot alpha\n")

    log = client.get(f"/deployments/{deployment_id}/console.log")

    assert log.status_code == 200
    assert log.content == b"boot alpha\n"
    assert log.headers["content-type"] == "application/octet-stream"
    disposition = f'attachment; filename="console-{deployment_id}.log"'
    assert log.headers["content-disposition"] == disposition
    assert log.headers["cache-control"] == "no-store"


def test_a_refused_download_comes_back_as_a_flash(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any], answer: Answer
) -> None:
    sign_in()
    deployment_id = stand["deployment"]["id"]
    answer("GET", f"/api/v1/deployments/{deployment_id}/console", {"detail": "no log"}, 404)

    log = client.get(f"/deployments/{deployment_id}/console.log")
    shown = client.get("/logs")

    assert log.status_code == 303
    assert log.headers["location"] == "/logs"
    assert "no log (404)" in shown.text
