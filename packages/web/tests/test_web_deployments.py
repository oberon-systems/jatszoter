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


def posted(calls: list[httpx.Request]) -> httpx.Request:
    return next(call for call in calls if call.method == "POST")


def test_the_list_names_every_deployment(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any]
) -> None:
    sign_in()

    page = client.get("/deployments")

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    for text in ("web-basics", "v3", "beta", "delta", "READY", "2025-10-09 08:53", "1 deployment"):
        assert text in page.text
    assert f'hx-get="/deployments/{stand["deployment"]["id"]}/stop"' in page.text


def test_a_refresh_swaps_the_table_alone(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any]
) -> None:
    sign_in()

    table = client.get("/deployments", headers={**HTMX, "hx-target": "deployments-table"})

    assert table.status_code == 200
    assert table.text.lstrip().startswith('<div id="deployments-table"')
    assert "<html" not in table.text


@pytest.mark.usefixtures("stand")
def test_an_empty_list_shows_its_empty_board(
    client: TestClient, sign_in: SignIn, answer: Answer, calls: list[httpx.Request]
) -> None:
    sign_in()
    answer("GET", "/api/v1/deployments", [])

    empty = client.get("/deployments")
    filtered = client.get("/deployments?status=FAILED")
    unknown = client.get("/deployments?status=bogus")

    assert "No deployments yet" in empty.text
    assert "No deployments match the filter" in filtered.text
    assert "Nothing is FAILED right now." in filtered.text
    assert "No deployments yet" in unknown.text
    listed = [
        call.url.params.get("status") for call in calls if call.url.path == "/api/v1/deployments"
    ]
    assert listed == [None, "FAILED", None]


@pytest.mark.usefixtures("stand")
def test_a_refused_list_shows_the_refusal(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    sign_in()
    answer("GET", "/api/v1/deployments", {"detail": "status is not valid"}, 422)

    page = client.get("/deployments")
    table = client.get("/deployments", headers={**HTMX, "hx-target": "deployments-table"})

    assert page.status_code == 422
    assert "status is not valid (422)" in page.text
    assert table.status_code == 422
    assert table.headers["hx-retarget"] == "#flashes"
    assert "status is not valid (422)" in table.text


@pytest.mark.usefixtures("stand")
def test_a_list_the_api_fails_to_answer_shows_the_failed_board(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    sign_in()
    answer("GET", "/api/v1/deployments", {}, 503)

    page = client.get("/deployments")

    assert page.status_code == 502
    assert "The list could not be loaded" in page.text
    assert "The API failed to answer. Try again in a moment. (503)" in page.text


def test_the_create_form_offers_what_a_deployment_can_name(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any]
) -> None:
    who = sign_in()

    page = client.get("/deployments/new")

    assert page.status_code == 200
    assert f'value="{who.csrf}"' in page.text
    for row in ("scenario", "trainee", "agent"):
        assert f'value="{stand[row]["id"]}"' in page.text
    assert "Any enabled agent" in page.text


@pytest.mark.usefixtures("stand")
def test_the_trainee_link_is_shown_once_on_create(
    client: TestClient,
    sign_in: SignIn,
    stand: dict[str, Any],
    answer: Answer,
    calls: list[httpx.Request],
) -> None:
    who = sign_in()
    created = {**stand["deployment"], "status": "PENDING", "link": {"token": "tok-alpha"}}
    answer("POST", "/api/v1/deployments", created, 201)
    form = {
        "csrf_token": who.csrf,
        "scenario_id": stand["scenario"]["id"],
        "trainee_id": stand["trainee"]["id"],
        "agent_id": "",
    }

    page = client.post("/deployments/new", data=form)
    later = client.get("/deployments")

    assert page.status_code == 200
    assert 'value="https://web.example.com/s/tok-alpha"' in page.text
    assert "web-basics v3 for beta is PENDING until an agent picks it up." in page.text
    assert json.loads(posted(calls).content) == {
        "scenario_id": stand["scenario"]["id"],
        "trainee_id": stand["trainee"]["id"],
    }
    assert "tok-alpha" not in later.text


@pytest.mark.usefixtures("stand")
def test_the_link_survives_names_that_fail_to_load(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any], answer: Answer
) -> None:
    who = sign_in()
    created = {**stand["deployment"], "status": "PENDING", "link": {"token": "tok-alpha"}}
    answer("POST", "/api/v1/deployments", created, 201)
    answer("GET", "/api/v1/users", {}, 503)
    form = {
        "csrf_token": who.csrf,
        "scenario_id": stand["scenario"]["id"],
        "trainee_id": stand["trainee"]["id"],
    }

    page = client.post("/deployments/new", data=form)

    assert page.status_code == 200
    assert "/s/tok-alpha" in page.text
    assert "Hand this link to the trainee." in page.text


@pytest.mark.usefixtures("stand")
def test_a_refused_create_shows_the_refusal_next_to_its_field(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any], answer: Answer
) -> None:
    who = sign_in()
    answer("POST", "/api/v1/deployments", {"detail": "user beta is disabled"}, 409)
    form = {
        "csrf_token": who.csrf,
        "scenario_id": stand["scenario"]["id"],
        "trainee_id": stand["trainee"]["id"],
    }

    page = client.post("/deployments/new", data=form)

    assert page.status_code == 409
    assert '<span class="field-error">user beta is disabled</span>' in page.text
    assert "user beta is disabled (409)" in page.text
    assert "/s/" not in page.text


def test_the_stop_dialog_names_the_deployment_and_where_to_return(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any]
) -> None:
    sign_in()
    headers = {**HTMX, "hx-current-url": "https://web.example.com/deployments?status=READY"}

    dialog = client.get(f"/deployments/{stand['deployment']['id']}/stop", headers=headers)

    assert dialog.status_code == 200
    assert dialog.text.lstrip().startswith("<dialog")
    assert "The VM of web-basics v3 for beta is destroyed" in dialog.text
    assert 'name="next" value="/deployments?status=READY"' in dialog.text


def test_a_stop_redirects_back_with_a_flash_shown_once(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any], answer: Answer
) -> None:
    who = sign_in()
    deployment_id = stand["deployment"]["id"]
    stopping = {**stand["deployment"], "status": "STOPPING"}
    answer("POST", f"/api/v1/deployments/{deployment_id}/stop", stopping)
    form = {"csrf_token": who.csrf, "next": "/deployments?status=READY"}

    stopped = client.post(f"/deployments/{deployment_id}/stop", data=form)
    by_htmx = client.post(f"/deployments/{deployment_id}/stop", data=form, headers=HTMX)
    shown = client.get("/deployments")
    again = client.get("/deployments")

    assert stopped.status_code == 303
    assert stopped.headers["location"] == "/deployments?status=READY"
    assert by_htmx.status_code == 204
    assert by_htmx.headers["hx-redirect"] == "/deployments?status=READY"
    assert "Stop requested" in shown.text
    assert "web-basics v3 for beta is STOPPING" in shown.text
    assert "Stop requested" not in again.text


def test_a_refused_stop_comes_back_as_a_flash(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any], answer: Answer
) -> None:
    who = sign_in()
    deployment_id = stand["deployment"]["id"]
    answer(
        "POST",
        f"/api/v1/deployments/{deployment_id}/stop",
        {"detail": "the deployment has ended"},
        409,
    )

    stopped = client.post(
        f"/deployments/{deployment_id}/stop",
        data={"csrf_token": who.csrf, "next": "https://beta.example.com/"},
    )
    shown = client.get("/deployments")

    assert stopped.status_code == 303
    assert stopped.headers["location"] == "/deployments"
    assert "the deployment has ended (409)" in shown.text
