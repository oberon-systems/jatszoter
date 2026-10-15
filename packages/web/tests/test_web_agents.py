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


def test_the_page_lists_registrations_and_agents(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any]
) -> None:
    sign_in()

    page = client.get("/agents")

    assert page.status_code == 200
    for text in ("Pending registrations", "gamma", "delta", "kvm", "2025-10-09 08:53:20"):
        assert text in page.text
    assert f'action="/agents/{stand["registration"]["id"]}/state"' in page.text
    assert f'hx-get="/agents/{stand["agent"]["id"]}/state"' in page.text


@pytest.mark.usefixtures("stand")
def test_a_refresh_swaps_the_tables_alone(client: TestClient, sign_in: SignIn) -> None:
    sign_in()

    tables = client.get("/agents", headers={**HTMX, "hx-target": "agents-tables"})

    assert tables.status_code == 200
    assert tables.text.lstrip().startswith('<div id="agents-tables"')
    assert "<html" not in tables.text


@pytest.mark.usefixtures("stand")
def test_no_agents_shows_the_empty_board(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    sign_in()
    answer("GET", "/api/v1/agents", {"registrations": [], "agents": []})

    page = client.get("/agents")

    assert "No agents yet" in page.text
    assert "Pending registrations" not in page.text


@pytest.mark.usefixtures("stand")
def test_a_refused_list_shows_the_refusal(
    client: TestClient, sign_in: SignIn, answer: Answer
) -> None:
    sign_in()
    answer("GET", "/api/v1/agents", {"detail": "agents are not listed"}, 422)

    page = client.get("/agents")

    assert page.status_code == 422
    assert "agents are not listed (422)" in page.text


def test_the_disable_dialog_names_the_agent(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any]
) -> None:
    sign_in()

    dialog = client.get(f"/agents/{stand['agent']['id']}/state", headers=HTMX)

    assert dialog.status_code == 200
    assert dialog.text.lstrip().startswith("<dialog")
    assert "delta" in dialog.text
    assert 'name="state" value="disabled"' in dialog.text


@pytest.mark.parametrize(
    ("row", "state", "title", "text"),
    [
        ("registration", "enabled", "Agent approved", "gamma is enabled"),
        ("agent", "disabled", "Agent disabled", "delta takes no deployments"),
        ("agent", "enabled", "Agent enabled", "delta takes deployments again."),
    ],
)
def test_a_state_change_redirects_with_its_flash(
    client: TestClient,
    sign_in: SignIn,
    stand: dict[str, Any],
    answer: Answer,
    calls: list[httpx.Request],
    row: str,
    state: str,
    title: str,
    text: str,
) -> None:
    who = sign_in()
    agent_id = stand[row]["id"]
    answer("POST", f"/api/v1/agents/{agent_id}/state", {**stand[row], "state": state})

    switched = client.post(
        f"/agents/{agent_id}/state", data={"csrf_token": who.csrf, "state": state}
    )
    shown = client.get("/agents")

    assert switched.status_code == 303
    assert switched.headers["location"] == "/agents"
    relayed = next(call for call in calls if call.method == "POST")
    assert json.loads(relayed.content) == {"state": state}
    assert title in shown.text
    assert text in shown.text


def test_a_refused_state_change_comes_back_as_a_flash(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any], answer: Answer
) -> None:
    who = sign_in()
    agent_id = stand["registration"]["id"]
    answer("POST", f"/api/v1/agents/{agent_id}/state", {"detail": "agent not found"}, 404)

    switched = client.post(
        f"/agents/{agent_id}/state", data={"csrf_token": who.csrf, "state": "disabled"}
    )
    shown = client.get("/agents")

    assert switched.status_code == 303
    assert "agent not found (404)" in shown.text
