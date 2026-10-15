from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jatszoter_web.cookies import Operator

Answer = Callable[..., None]
SignIn = Callable[..., Operator]

HTMX = {"hx-request": "true"}
ENDED = "00000000-0000-4000-8000-0000000000d2"


@pytest.fixture
def ended(stand: dict[str, Any], answer: Answer) -> dict[str, Any]:
    """A COMPLETED deployment next to the READY one, holding a log and a recording."""
    row = {**stand["deployment"], "id": ENDED, "status": "COMPLETED", "ended_at": 1760003600}
    answer("GET", "/api/v1/deployments", [stand["deployment"], row])
    answer("GET", f"/api/v1/deployments/{ENDED}", row)
    answer(
        "GET",
        "/api/v1/storage",
        {
            "deployments": [
                {
                    "deployment_id": stand["deployment"]["id"],
                    "console_bytes": 2048,
                    "recording_bytes": 0,
                    "recordings": 0,
                },
                {
                    "deployment_id": ENDED,
                    "console_bytes": 1024,
                    "recording_bytes": 3 * 1024 * 1024,
                    "recordings": 1,
                },
            ],
            "scenario_bytes": 512,
            "total_bytes": 3 * 1024 * 1024 + 3584,
        },
    )
    return row


@pytest.mark.usefixtures("ended")
def test_the_page_shows_what_each_deployment_holds(client: TestClient, sign_in: SignIn) -> None:
    sign_in()

    page = client.get("/storage")

    assert page.status_code == 200
    for text in ("3 KiB", "3.0 MiB", "512 B", "2025-10-09 09:53", "running"):
        assert text in page.text
    assert f'hx-get="/storage/{ENDED}/purge"' in page.text
    assert page.text.count("/purge") == 1


@pytest.mark.usefixtures("stand")
def test_nothing_stored_shows_the_empty_board(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    sign_in()
    answer("GET", "/api/v1/storage", {"deployments": [], "scenario_bytes": 0, "total_bytes": 0})

    assert "Nothing is stored yet" in client.get("/storage").text


@pytest.mark.usefixtures("stand")
def test_a_refused_page_shows_the_refusal(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    sign_in()
    answer("GET", "/api/v1/storage", {"detail": "storage is not listed"}, 422)

    page = client.get("/storage")

    assert page.status_code == 422
    assert "storage is not listed (422)" in page.text


@pytest.mark.usefixtures("ended")
def test_the_purge_dialog_names_what_is_deleted(client: TestClient, sign_in: SignIn) -> None:
    sign_in()

    dialog = client.get(f"/storage/{ENDED}/purge", headers=HTMX)

    assert dialog.status_code == 200
    assert dialog.text.lstrip().startswith("<dialog")
    assert "Purge web-basics v3 for beta?" in dialog.text
    assert "Its console log and 1 recording are deleted" in dialog.text


@pytest.mark.usefixtures("ended")
def test_a_purge_redirects_with_its_flash(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    who = sign_in()
    answer("POST", f"/api/v1/deployments/{ENDED}/purge", {})

    purged = client.post(f"/storage/{ENDED}/purge", data={"csrf_token": who.csrf})
    shown = client.get("/storage")

    assert purged.status_code == 303
    assert purged.headers["location"] == "/storage"
    assert "Purged" in shown.text
    assert "The console log and recordings of web-basics v3 for beta are deleted." in shown.text


@pytest.mark.usefixtures("ended")
def test_a_refused_purge_comes_back_as_a_flash(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    who = sign_in()
    detail = "the deployment is still running"
    answer("POST", f"/api/v1/deployments/{ENDED}/purge", {"detail": detail}, 409)

    purged = client.post(f"/storage/{ENDED}/purge", data={"csrf_token": who.csrf})
    shown = client.get("/storage")

    assert purged.status_code == 303
    assert f"{detail} (409)" in shown.text
