import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from starlette.types import Message, Receive, Scope, Send

from jatszoter_api.app import create_app
from jatszoter_api.middleware import AnswerHeaders
from jatszoter_api.models import Role, State, User
from jatszoter_api.settings import Settings

PASSWORD = "Correct horse battery"


def assert_not_stored(answer: httpx.Response) -> None:
    assert answer.headers.get_list("cache-control") == ["no-store"]
    assert answer.headers.get_list("x-content-type-options") == ["nosniff"]


def test_a_login_answer_is_not_stored(api: TestClient, session: Session) -> None:
    User(
        session=session,
        name="alpha",
        role=Role.OPERATOR,
        state=State.ENABLED,
        password=PASSWORD,
        password_changed=1,
    ).save()

    answer = api.post("/api/v1/auth/login", json={"name": "alpha", "password": PASSWORD})

    assert answer.status_code == 200
    assert_not_stored(answer)


def test_a_registration_answer_is_not_stored(api: TestClient) -> None:
    answer = api.post("/api/v1/agents/register", json={"name": "alpha"})

    assert answer.status_code == 201
    assert_not_stored(answer)


def test_a_refused_claim_is_not_stored(api: TestClient) -> None:
    answer = api.post("/api/v1/agents/claim", json={"code": "alpha"})

    assert answer.status_code == 401
    assert_not_stored(answer)


@pytest.mark.parametrize(
    ("path", "status"),
    [("/healthz", 200), ("/missing", 404), ("/s/alpha", 404)],
)
def test_any_other_answer_is_not_stored(api: TestClient, path: str, status: int) -> None:
    answer = api.get(path)

    assert answer.status_code == status
    assert_not_stored(answer)


def test_a_body_refused_by_size_is_not_stored(api: TestClient) -> None:
    answer = api.post(
        "/api/v1/agents/register",
        content=b"{" + b" " * (2 * 1024 * 1024),
        headers={"content-type": "application/json"},
    )

    assert answer.status_code == 413
    assert_not_stored(answer)


def test_a_server_error_is_not_stored(session: Session, settings: Settings) -> None:
    app = create_app(settings)

    def broken() -> None:
        raise RuntimeError("alpha")

    app.add_api_route("/broken", broken)

    with TestClient(app, raise_server_exceptions=False) as client:
        answer = client.get("/broken")

    assert answer.status_code == 500
    assert_not_stored(answer)


def test_a_socket_passes_untouched() -> None:
    seen: list[Send] = []

    async def app(_: Scope, __: Receive, send: Send) -> None:
        seen.append(send)

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(_: Message) -> None:
        raise AssertionError("the app behind the headers answers nothing here")

    scope: Scope = {"type": "websocket", "path": "/alpha", "headers": []}
    asyncio.run(AnswerHeaders(app)(scope, receive, send))

    assert seen == [send]
