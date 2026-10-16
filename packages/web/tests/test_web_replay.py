from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from jatszoter_web.cookies import LINK_COOKIE, Operator
from jatszoter_web.middleware import SECURITY_HEADERS, WASM_POLICY
from jatszoter_web.replay import markers

Answer = Callable[..., None]
Routes = dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]]
SignIn = Callable[..., Operator]

HOST = "web.example.com"
AT = 1760000000
LINK = {
    "id": "00000000-0000-4000-8000-0000000000e1",
    "user_id": "00000000-0000-4000-8000-0000000000b1",
    "role": "trainee",
    "created_at": AT + 60,
    "opened_at": AT + 120,
    "revoked_at": AT + 3711,
}
RECORDING = {
    "id": "00000000-0000-4000-8000-000000000101",
    "link_id": LINK["id"],
    "created_at": AT + 120,
    "size_bytes": 1887437,
    "duration_seconds": 3591.0,
}
CAST_API = f"/api/v1/recordings/{RECORDING['id']}.cast"
CAST = b"\n".join(
    (
        b'{"version": 2, "width": 80, "height": 24, "timestamp": 1760000120}',
        b'[0.0, "o", "trainee@alpha:~$ "]',
        b'[4.031, "r", "140x40"]',
        b'[9.87, "m", "control operator"]',
        b'[61.2, "m", "check failed: nginx still does not serve the app page"]',
        b'[61.3, "o", "par',
    )
)


@pytest.fixture
def recorded(stand: dict[str, Any], answer: Answer, routes: Routes) -> str:
    """The stand's deployment with one closed recording; answers the page's path."""
    deployment_id = stand["deployment"]["id"]
    base = f"/api/v1/deployments/{deployment_id}"
    answer("GET", f"{base}/links", [LINK])
    answer("GET", f"{base}/recordings", [RECORDING])
    routes[("GET", CAST_API)] = lambda _: httpx.Response(200, content=CAST)
    return f"/deployments/{deployment_id}/recordings/{RECORDING['id']}"


def cast_calls(calls: list[httpx.Request]) -> list[httpx.Request]:
    return [call for call in calls if call.url.path == CAST_API]


def test_a_recording_plays_with_its_markers_as_jump_points(
    client: TestClient,
    sign_in: SignIn,
    recorded: str,
    calls: list[httpx.Request],
    signed_by: Callable[[httpx.Request, str], bool],
) -> None:
    sign_in()

    page = client.get(recorded)

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["content-security-policy"] == WASM_POLICY
    text = page.text
    for shown in (
        "Replay: web-basics v3 for beta",
        "00:59:51, 1.8 MiB",
        '<span class="body-strong">beta</span>',
        f'data-cast="{recorded}.cast"',
        "/static/vendor/asciinema-player-3.17.0/asciinema-player.min.js",
        "/static/replay.js",
        'data-seek="9.87"',
        "00:00:09",
        "control operator",
        'data-seek="61.2"',
        "00:01:01",
        "check failed: nginx still does not serve the app page",
    ):
        assert shown in text
    assert text.index("control operator") < text.index("check failed")
    assert "trainee@alpha" not in text and "140x40" not in text
    assert "No markers" not in text
    assert signed_by(cast_calls(calls)[0], "signing-key-alpha")


def test_markers_are_the_m_events_and_nothing_else() -> None:
    cast = b"\n".join(
        (
            b'{"version": 2, "width": 80, "height": 24}',
            b'[1, "m", "control trainee"]',
            b'[2.5, "o", "check passed"]',
            b'[3, "m", 7]',
            b'["4", "m", "check passed"]',
            b"not json",
            b'{"m": "control operator"}',
            b'[5.5, "m", "recording limit reached"]',
            b'[6, "m", "che',
        )
    )

    assert markers(cast) == [(1.0, "control trainee"), (5.5, "recording limit reached")]
    assert markers(b'[1, "m", "the header line is never an event"]') == []


def test_a_live_recording_says_so(
    client: TestClient, sign_in: SignIn, recorded: str, stand: dict[str, Any], answer: Answer
) -> None:
    sign_in()
    live = {**RECORDING, "size_bytes": 0, "duration_seconds": 0}
    base = f"/api/v1/deployments/{stand['deployment']['id']}"
    answer("GET", f"{base}/links", [{**LINK, "revoked_at": None}])
    answer("GET", f"{base}/recordings", [live])

    page = client.get(recorded)

    assert page.status_code == 200
    assert "still recording" in page.text
    assert "0 B" not in page.text and "00:00:00" not in page.text
    assert "data-player" in page.text


def test_a_recording_without_markers_shows_the_empty_board(
    client: TestClient, sign_in: SignIn, recorded: str, routes: Routes
) -> None:
    sign_in()
    header = CAST.splitlines()[0]
    routes[("GET", CAST_API)] = lambda _: httpx.Response(200, content=header + b"\n")

    page = client.get(recorded)

    assert "No markers" in page.text
    assert "Nothing changed control and no check settled while this was recorded." in page.text
    assert "data-seek" not in page.text


def test_a_recording_of_another_deployment_is_not_found(
    client: TestClient,
    sign_in: SignIn,
    recorded: str,
    stand: dict[str, Any],
    answer: Answer,
    calls: list[httpx.Request],
) -> None:
    sign_in()
    answer("GET", f"/api/v1/deployments/{stand['deployment']['id']}/recordings", [])

    page = client.get(recorded)
    cast = client.get(f"{recorded}.cast")

    assert page.status_code == 404
    assert "recording not found (404)" in page.text
    assert "Replay: web-basics v3 for beta" in page.text
    assert "data-player" not in page.text and "Recorded" not in page.text
    assert page.headers["content-security-policy"] == SECURITY_HEADERS["content-security-policy"]
    assert cast.status_code == 404
    assert cast.content == b""
    assert cast_calls(calls) == []


def test_a_recording_whose_file_is_gone_is_not_found(
    client: TestClient, sign_in: SignIn, recorded: str, answer: Answer
) -> None:
    sign_in()
    answer("GET", CAST_API, {"detail": "recording has no file"}, 404)

    page = client.get(recorded)
    cast = client.get(f"{recorded}.cast")

    assert page.status_code == 404
    assert "recording not found (404)" in page.text
    assert "Recorded" in page.text
    assert "data-player" not in page.text and "asciinema-player" not in page.text
    assert cast.status_code == 404
    assert cast.headers["cache-control"] == "no-store"


def test_an_unreachable_api_shows_the_recording_could_not_be_loaded(
    client: TestClient, sign_in: SignIn, recorded: str, routes: Routes
) -> None:
    sign_in()

    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    routes[("GET", CAST_API)] = down

    page = client.get(recorded)

    assert page.status_code == 502
    assert "The API cannot be reached. Nothing was changed" in page.text
    assert "The recording could not be loaded." in page.text
    assert "No markers" in page.text
    assert "data-player" not in page.text and "asciinema-player" not in page.text
    assert page.headers["content-security-policy"] == SECURITY_HEADERS["content-security-policy"]


def test_the_cast_reaches_the_browser_through_a_signed_call(
    client: TestClient,
    sign_in: SignIn,
    recorded: str,
    calls: list[httpx.Request],
    signed_by: Callable[[httpx.Request, str], bool],
) -> None:
    sign_in()

    cast = client.get(f"{recorded}.cast")

    assert cast.status_code == 200
    assert cast.content == CAST
    assert cast.headers["content-type"] == "application/x-asciicast"
    assert cast.headers["cache-control"] == "no-store"
    assert signed_by(cast_calls(calls)[0], "signing-key-alpha")


@pytest.mark.parametrize("suffix", ["", ".cast"])
def test_a_link_gets_nothing_from_the_replay(
    client: TestClient, recorded: str, calls: list[httpx.Request], suffix: str
) -> None:
    client.cookies.set(LINK_COOKIE, "link-session-alpha", domain=HOST)

    response = client.get(recorded + suffix)

    assert response.status_code == 303
    assert response.headers["location"] == f"/login?next={recorded}{suffix}"
    assert calls == []


def test_the_api_cast_route_is_not_relayed(
    client: TestClient, recorded: str, calls: list[httpx.Request]
) -> None:
    client.cookies.set(LINK_COOKIE, "link-session-alpha", domain=HOST)

    response = client.get(CAST_API)

    assert response.status_code == 404
    assert calls == []
