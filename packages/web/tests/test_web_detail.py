import json
import re
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from jatszoter_web.cookies import CSRF_COOKIE, Operator

Answer = Callable[..., None]
Routes = dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]]
SignIn = Callable[..., Operator]

HTMX = {"hx-request": "true"}
AT = 1760000000
VIEWER = {
    "id": "00000000-0000-4000-8000-0000000000b2",
    "name": "gamma",
    "role": "viewer",
    "state": "enabled",
    "comment": None,
    "created_at": AT,
}
TRAINEE_LINK = {
    "id": "00000000-0000-4000-8000-0000000000e1",
    "user_id": "00000000-0000-4000-8000-0000000000b1",
    "role": "trainee",
    "created_at": AT + 60,
    "opened_at": AT + 120,
    "revoked_at": None,
}
VIEWER_LINK = {
    "id": "00000000-0000-4000-8000-0000000000e2",
    "user_id": VIEWER["id"],
    "role": "viewer",
    "created_at": AT + 90,
    "opened_at": None,
    "revoked_at": AT + 600,
}
PASSED = {
    "id": "00000000-0000-4000-8000-0000000000f1",
    "status": "passed",
    "exit_code": 0,
    "message": "nginx answers on port 443",
    "detail": None,
    "created_at": AT + 300,
    "finished_at": AT + 310,
}
FAILED = {
    **PASSED,
    "id": "00000000-0000-4000-8000-0000000000f2",
    "status": "failed",
    "exit_code": 1,
    "message": "nginx does not answer on port 443",
    "detail": "curl: (7) Failed to connect to 127.0.0.1 port 443",
    "created_at": AT + 200,
}
RECORDING = {
    "id": "00000000-0000-4000-8000-000000000101",
    "link_id": TRAINEE_LINK["id"],
    "created_at": AT + 120,
    "size_bytes": 1887437,
    "duration_seconds": 3591.0,
}


@pytest.fixture
def detail(stand: dict[str, Any], answer: Answer) -> dict[str, Any]:
    """The stand's READY deployment with every field the detail page shows, and its rows."""
    row = {
        **stand["deployment"],
        "reason": None,
        "cpus": 2,
        "memory_mib": 2048,
        "disk_gib": 10,
        "bootstrap_seconds": 900,
        "check_seconds": 60,
        "session_minutes": 60,
        "ready_at": AT + 60,
    }
    base = f"/api/v1/deployments/{row['id']}"
    answer("GET", base, row)
    answer("GET", "/api/v1/users", [stand["trainee"], VIEWER])
    answer("GET", f"{base}/links", [VIEWER_LINK, TRAINEE_LINK])
    answer("GET", f"{base}/checks", [FAILED, PASSED])
    answer("GET", f"{base}/recordings", [RECORDING])
    return row


def with_status(answer: Answer, row: dict[str, Any], **changes: Any) -> dict[str, Any]:
    changed = {**row, **changes}
    answer("GET", f"/api/v1/deployments/{row['id']}", changed)
    return changed


def test_a_ready_deployment_shows_its_terminal_links_checks_and_recordings(
    client: TestClient, sign_in: SignIn, detail: dict[str, Any]
) -> None:
    sign_in()

    page = client.get(f"/deployments/{detail['id']}")

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    text = page.text
    for shown in (
        "web-basics v3 for beta",
        "Status, links and checks refresh every 5 seconds",
        "2 CPUs, 2048 MiB, 10 GiB",
        "bootstrap 900 s, check 60 s, session 60 min",
        "2025-10-09 09:54",
        "delta",
        "Stop deployment",
        "Issue link",
        'revoked <span class="mono-small">2025-10-09 09:03:20</span>',
        "not opened",
        "curl: (7) Failed to connect to 127.0.0.1 port 443",
        "recording, replay after the link closes",
    ):
        assert shown in text
    assert f'data-socket="/deployments/{detail["id"]}/attach"' in text
    assert 'data-holder-trainee="trainee beta"' in text
    assert f'hx-post="/deployments/{detail["id"]}/control"' in text
    assert text.count(">Revoke</button>") == 1
    checks = text[text.index(">Checks</h2>") :]
    assert checks.index("nginx answers on port 443") < checks.index("nginx does not answer")
    links = text[text.index(">Links</h2>") :]
    assert links.index("<td>beta</td>") < links.index("<td>gamma</td>")
    assert "Replay" not in text


def test_the_page_loads_only_own_files_and_runs_no_inline_code(
    client: TestClient, sign_in: SignIn, detail: dict[str, Any]
) -> None:
    sign_in()

    page = client.get(f"/deployments/{detail['id']}").text

    for reference in re.findall(r'<(?:script|link)[^>]*(?:src|href)="([^"]+)"', page):
        assert reference.startswith("/static/")
        assert client.get(reference).status_code == 200
    assert "/static/vendor/xterm-6.0.0/xterm.js" in page
    assert "<style" not in page and "style=" not in page
    assert re.search(r"<script(?![^>]*\bsrc=)", page) is None


def test_a_closed_recording_offers_its_replay(
    client: TestClient, sign_in: SignIn, detail: dict[str, Any], answer: Answer
) -> None:
    sign_in()
    revoked = {**TRAINEE_LINK, "revoked_at": AT + 3700}
    answer("GET", f"/api/v1/deployments/{detail['id']}/links", [revoked])

    page = client.get(f"/deployments/{detail['id']}").text

    assert f'href="/deployments/{detail["id"]}/recordings/{RECORDING["id"]}">Replay' in page
    assert "1.8 MiB" in page and "00:59:51" in page
    assert ">Revoke</button>" not in page


@pytest.mark.parametrize(
    ("status", "note"),
    [
        ("BOOTSTRAPPING", "The VM is being prepared. The terminal opens when the deployment is"),
        ("STOPPING", "The deployment has ended and the terminal is closed."),
    ],
)
def test_a_deployment_not_ready_shows_no_terminal(
    client: TestClient,
    sign_in: SignIn,
    detail: dict[str, Any],
    answer: Answer,
    status: str,
    note: str,
) -> None:
    sign_in()
    with_status(answer, detail, status=status, ready_at=None)

    page = client.get(f"/deployments/{detail['id']}").text

    assert note in page
    assert "data-socket" not in page and "data-control" not in page
    assert re.search(r'hx-post="[^"]+/check"[^>]*disabled', page, re.S)


@pytest.mark.parametrize(
    ("status", "note"),
    [
        ("COMPLETED", "The deployment has ended and the terminal is closed."),
        ("FAILED", "The deployment failed before the terminal opened."),
    ],
)
def test_an_ended_deployment_offers_no_action_but_its_log(
    client: TestClient,
    sign_in: SignIn,
    detail: dict[str, Any],
    answer: Answer,
    status: str,
    note: str,
) -> None:
    sign_in()
    with_status(answer, detail, status=status, reason="expired", ended_at=AT + 3700)
    answer("GET", f"/api/v1/deployments/{detail['id']}/checks", [])

    page = client.get(f"/deployments/{detail['id']}").text

    assert note in page
    assert "Ended 2025-10-09 09:55" in page
    assert "expired" in page
    assert f'href="/deployments/{detail["id"]}/console.log"' in page
    assert "No check was requested before the deployment ended." in page
    for offered in ("Stop deployment", "Issue link", "Request check", "data-socket"):
        assert offered not in page


def test_a_refresh_swaps_the_sections_and_the_summary(
    client: TestClient, sign_in: SignIn, detail: dict[str, Any]
) -> None:
    sign_in()
    headers = {**HTMX, "hx-target": "deployment-live"}

    live = client.get(f"/deployments/{detail['id']}?seen=READY", headers=headers)

    assert live.status_code == 200
    assert live.text.lstrip().startswith('<div id="deployment-live"')
    assert "<html" not in live.text
    assert 'id="deployment-summary"' in live.text and 'id="check-bar"' in live.text
    assert live.text.count('hx-swap-oob="true"') == 2


def test_a_new_status_reloads_the_page(
    client: TestClient, sign_in: SignIn, detail: dict[str, Any]
) -> None:
    sign_in()
    headers = {**HTMX, "hx-target": "deployment-live"}

    live = client.get(f"/deployments/{detail['id']}?seen=BOOTSTRAPPING", headers=headers)

    assert live.status_code == 204
    assert live.headers["hx-refresh"] == "true"


@pytest.mark.usefixtures("stand")
def test_an_unknown_deployment_shows_the_refusal(client: TestClient, sign_in: SignIn) -> None:
    sign_in()

    page = client.get("/deployments/00000000-0000-4000-8000-0000000000ff")

    assert page.status_code == 404
    assert "not found (404)" in page.text


def test_a_check_is_asked_for_once_and_the_bar_shows_it_pending(
    client: TestClient,
    sign_in: SignIn,
    detail: dict[str, Any],
    answer: Answer,
    calls: list[httpx.Request],
) -> None:
    who = sign_in()
    checks = f"/api/v1/deployments/{detail['id']}/checks"
    pending = {**PASSED, "status": "pending", "message": None, "created_at": AT + 400}
    answer("POST", checks, pending, 201)
    answer("GET", checks, [FAILED, PASSED, pending])

    bar = client.post(
        f"/deployments/{detail['id']}/check", headers={**HTMX, "x-csrf-token": who.csrf}
    )

    assert bar.status_code == 200
    assert bar.text.lstrip().startswith('<div id="check-bar"')
    assert "waiting for the agent" in bar.text
    assert re.search(r'hx-post="[^"]+/check"[^>]*disabled', bar.text, re.S)
    assert [call.url.path for call in calls if call.method == "POST"] == [checks]


def test_a_pending_check_refuses_another_as_its_board_says(
    client: TestClient, sign_in: SignIn, detail: dict[str, Any], answer: Answer
) -> None:
    who = sign_in()
    refused = {"detail": f"deployment {detail['id']} has a pending check"}
    answer("POST", f"/api/v1/deployments/{detail['id']}/checks", refused, 409)

    bar = client.post(
        f"/deployments/{detail['id']}/check", headers={**HTMX, "x-csrf-token": who.csrf}
    )

    assert bar.status_code == 409
    assert bar.headers["hx-retarget"] == "#flashes"
    assert "a check is already pending (409)" in bar.text


def test_a_check_without_the_csrf_token_reaches_no_api_route(
    client: TestClient, sign_in: SignIn, detail: dict[str, Any], calls: list[httpx.Request]
) -> None:
    sign_in()

    refused = client.post(f"/deployments/{detail['id']}/check", headers=HTMX)

    assert refused.status_code == 403
    assert not [call for call in calls if call.method == "POST"]


@pytest.mark.parametrize("holder", ["operator", "trainee"])
def test_control_is_taken_and_given_back_through_a_signed_call(
    client: TestClient,
    sign_in: SignIn,
    detail: dict[str, Any],
    answer: Answer,
    calls: list[httpx.Request],
    signed_by: Callable[[httpx.Request, str], bool],
    holder: str,
) -> None:
    who = sign_in()
    control = f"/api/v1/deployments/{detail['id']}/control"
    answer("POST", control, {"holder": holder})

    taken = client.post(
        f"/deployments/{detail['id']}/control",
        data={"holder": holder},
        headers={**HTMX, "x-csrf-token": who.csrf},
    )

    assert taken.status_code == 204
    sent = next(call for call in calls if call.url.path == control)
    assert json.loads(sent.content) == {"holder": holder}
    assert signed_by(sent, who.key)


def test_a_refused_control_shows_the_refusal(
    client: TestClient, sign_in: SignIn, detail: dict[str, Any], answer: Answer
) -> None:
    who = sign_in()
    refused = {"detail": "deployment is STOPPING"}
    answer("POST", f"/api/v1/deployments/{detail['id']}/control", refused, 409)

    taken = client.post(
        f"/deployments/{detail['id']}/control",
        data={"holder": "operator"},
        headers={**HTMX, "x-csrf-token": who.csrf},
    )

    assert taken.status_code == 409
    assert "deployment is STOPPING (409)" in taken.text


def test_the_link_dialog_offers_the_enabled_users(
    client: TestClient,
    sign_in: SignIn,
    detail: dict[str, Any],
    stand: dict[str, Any],
    answer: Answer,
) -> None:
    sign_in()
    disabled = {**VIEWER, "id": "00000000-0000-4000-8000-0000000000b3", "name": "delta"}
    answer("GET", "/api/v1/users", [stand["trainee"], VIEWER, {**disabled, "state": "disabled"}])

    dialog = client.get(f"/deployments/{detail['id']}/links", headers=HTMX)

    assert dialog.status_code == 200
    assert dialog.text.lstrip().startswith("<dialog")
    assert "beta (trainee)" in dialog.text and "gamma (viewer)" in dialog.text
    assert "delta (viewer)" not in dialog.text


def test_an_issued_link_is_shown_once(
    client: TestClient,
    sign_in: SignIn,
    detail: dict[str, Any],
    answer: Answer,
    calls: list[httpx.Request],
) -> None:
    who = sign_in()
    links = f"/api/v1/deployments/{detail['id']}/links"
    issued = {**VIEWER_LINK, "revoked_at": None, "token": "tok-gamma"}
    answer("POST", links, issued, 201)

    shown = client.post(
        links.removeprefix("/api/v1"),
        data={"csrf_token": who.csrf, "user_id": VIEWER["id"]},
        headers=HTMX,
    )
    page = client.get(f"/deployments/{detail['id']}")

    assert shown.status_code == 200
    assert shown.headers["cache-control"] == "no-store"
    assert "Link for gamma" in shown.text
    assert 'value="https://web.example.com/s/tok-gamma"' in shown.text
    posted = next(call for call in calls if call.method == "POST")
    assert json.loads(posted.content) == {"user_id": VIEWER["id"]}
    assert "tok-gamma" not in page.text


def test_a_second_trainee_link_is_refused_next_to_the_user(
    client: TestClient,
    sign_in: SignIn,
    detail: dict[str, Any],
    stand: dict[str, Any],
    answer: Answer,
) -> None:
    who = sign_in()
    refused = {"detail": f"deployment {detail['id']} holds a live trainee link"}
    answer("POST", f"/api/v1/deployments/{detail['id']}/links", refused, 409)

    dialog = client.post(
        f"/deployments/{detail['id']}/links",
        data={"csrf_token": who.csrf, "user_id": stand["trainee"]["id"]},
        headers=HTMX,
    )

    assert dialog.status_code == 409
    assert '<span class="field-error">deployment' in dialog.text
    assert "holds a live trainee link (409)" in dialog.text
    assert "/s/" not in dialog.text


def test_the_revoke_dialog_names_the_link_and_where_to_return(
    client: TestClient, sign_in: SignIn, detail: dict[str, Any]
) -> None:
    sign_in()
    page = f"https://web.example.com/deployments/{detail['id']}"

    dialog = client.get(
        f"/links/{VIEWER_LINK['id']}/revoke?deployment_id={detail['id']}",
        headers={**HTMX, "hx-current-url": page},
    )

    assert dialog.status_code == 200
    assert "The link of gamma stops working at once" in dialog.text
    assert f'name="next" value="/deployments/{detail["id"]}"' in dialog.text


def test_a_revoke_returns_to_the_deployment_with_a_flash(
    client: TestClient, sign_in: SignIn, detail: dict[str, Any], answer: Answer
) -> None:
    who = sign_in()
    answer("POST", f"/api/v1/links/{VIEWER_LINK['id']}/revoke", VIEWER_LINK)
    back = f"/deployments/{detail['id']}"

    revoked = client.post(
        f"/links/{VIEWER_LINK['id']}/revoke", data={"csrf_token": who.csrf, "next": back}
    )
    shown = client.get(back)

    assert revoked.status_code == 303
    assert revoked.headers["location"] == back
    assert "Link revoked" in shown.text
    assert "The link of gamma no longer opens the terminal." in shown.text


def test_a_refused_revoke_comes_back_as_a_flash(
    client: TestClient, sign_in: SignIn, detail: dict[str, Any], answer: Answer
) -> None:
    who = sign_in()
    refused = {"detail": "link is not known"}
    answer("POST", f"/api/v1/links/{VIEWER_LINK['id']}/revoke", refused, 404)
    back = f"/deployments/{detail['id']}"

    revoked = client.post(
        f"/links/{VIEWER_LINK['id']}/revoke", data={"csrf_token": who.csrf, "next": back}
    )
    shown = client.get(back)

    assert revoked.headers["location"] == back
    assert "link is not known (404)" in shown.text


def test_the_log_page_draws_the_log_read_only(
    client: TestClient, sign_in: SignIn, detail: dict[str, Any]
) -> None:
    sign_in()

    page = client.get(f"/logs/{detail['id']}")

    assert page.status_code == 200
    assert "web-basics v3 for beta" in page.text
    assert "2 KiB" in page.text
    assert f'data-log="/deployments/{detail["id"]}/console.log"' in page.text
    assert "data-socket" not in page.text


def test_an_empty_log_shows_its_empty_board(
    client: TestClient, sign_in: SignIn, detail: dict[str, Any], answer: Answer
) -> None:
    sign_in()
    answer("GET", "/api/v1/storage", {"deployments": [], "scenario_bytes": 0, "total_bytes": 0})

    page = client.get(f"/logs/{detail['id']}").text

    assert "No console output yet: the log starts when the VM boots." in page
    assert "data-log" not in page and ">Download</a>" not in page


@pytest.mark.usefixtures("stand")
def test_an_unknown_log_returns_to_the_logs_with_a_flash(
    client: TestClient, sign_in: SignIn
) -> None:
    sign_in()

    page = client.get("/logs/00000000-0000-4000-8000-0000000000ff")
    shown = client.get("/logs")

    assert page.status_code == 303
    assert page.headers["location"] == "/logs"
    assert "not found (404)" in shown.text


def test_the_logs_list_offers_the_log_page(
    client: TestClient, sign_in: SignIn, stand: dict[str, Any]
) -> None:
    sign_in()

    page = client.get("/logs").text

    assert f'href="/logs/{stand["deployment"]["id"]}">View log' in page


def test_the_trainee_page_is_a_shell_without_detail_or_navigation(
    client: TestClient, calls: list[httpx.Request]
) -> None:
    page = client.get("/t/00000000-0000-4000-8000-0000000000d1")

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    token = page.cookies.get(CSRF_COOKIE)
    assert token and f'<meta name="csrf-token" content="{token}">' in page.text
    assert "/static/trainee.js" in page.text and "/static/terminal.js" in page.text
    socket = "/api/v1/deployments/00000000-0000-4000-8000-0000000000d1/attach"
    assert f'data-socket="{socket}"' in page.text
    assert "Detail" not in page.text
    assert 'class="navigation"' not in page.text and "hx-headers" not in page.text
    assert not calls


def test_the_trainee_script_never_reads_a_check_detail(client: TestClient) -> None:
    script = client.get("/static/trainee.js").text

    assert "check.detail" not in script and ".exit_code" not in script
