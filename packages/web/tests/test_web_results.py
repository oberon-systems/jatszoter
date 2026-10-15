from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jatszoter_web.cookies import Operator

Answer = Callable[..., None]
SignIn = Callable[..., Operator]

HTMX = {"hx-request": "true"}


@pytest.fixture
def checked(stand: dict[str, Any], answer: Answer) -> None:
    """Two checks of the READY deployment, newest first, and one of a deployment not listed."""
    deployment_id = stand["deployment"]["id"]
    answer(
        "GET",
        "/api/v1/checks",
        [
            {"deployment_id": deployment_id, "status": "failed", "message": "port 80 closed"},
            {"deployment_id": "00000000-0000-4000-8000-0000000000d9", "status": "passed"},
            {"deployment_id": deployment_id, "status": "passed", "message": "all good"},
        ],
    )


@pytest.mark.usefixtures("checked")
def test_one_row_per_deployment_shows_its_last_check(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any]
) -> None:
    sign_in()

    page = client.get("/results")

    assert page.status_code == 200
    for text in ("web-basics", "beta", "READY", "failed", "port 80 closed"):
        assert text in page.text
    assert "all good" not in page.text
    assert '<td class="mono-small">2</td>' in page.text
    assert page.text.count('href="/deployments/') == 1
    assert f'<option value="{stand["trainee"]["id"]}"' in page.text


@pytest.mark.usefixtures("checked")
def test_the_filters_keep_the_matching_rows(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any]
) -> None:
    sign_in()
    trainee = stand["trainee"]["id"]

    kept = client.get(
        f"/results?scenario=web-basics&trainee={trainee}",
        headers={**HTMX, "hx-target": "results-table"},
    )
    dropped = client.get("/results?scenario=alpha-basics")

    assert "<html" not in kept.text
    assert "port 80 closed" in kept.text
    assert "No results match the filters" in dropped.text
    assert "No deployment of alpha-basics has a check." in dropped.text


@pytest.mark.usefixtures("stand")
def test_no_checks_shows_the_empty_board(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    sign_in()
    answer("GET", "/api/v1/checks", [])

    assert "No results yet" in client.get("/results").text


@pytest.mark.usefixtures("stand")
def test_a_viewer_without_the_role_sees_not_allowed(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    sign_in()
    answer("GET", "/api/v1/checks", {"detail": "not allowed"}, 403)

    page = client.get("/results")
    table = client.get("/results", headers={**HTMX, "hx-target": "results-table"})

    assert page.status_code == 403
    assert "This action is not allowed for your account. (403)" in page.text
    assert table.status_code == 403
    assert table.headers["hx-retarget"] == "#flashes"


@pytest.mark.usefixtures("stand")
def test_a_refused_page_shows_the_refusal(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    sign_in()
    answer("GET", "/api/v1/checks", {"detail": "checks are not listed"}, 422)

    page = client.get("/results")

    assert page.status_code == 422
    assert "checks are not listed (422)" in page.text
