import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.types import Message, Receive, Scope, Send

from jatszoter_api.middleware import BODY_LIMIT, BodyLimit

LARGE = 2 * 1024 * 1024
WIDE = {("POST", "/alpha"): 2 * BODY_LIMIT}


def read_through(chunks: list[bytes], headers: list[tuple[bytes, bytes]] | None = None) -> bytes:
    """The body an app behind the limit reads from `chunks`."""
    seen: list[bytes] = []
    messages = iter(chunks)

    async def app(scope: Scope, receive: Receive, _: Send) -> None:
        seen.append(await Request(scope, receive).body())

    async def receive() -> Message:
        chunk = next(messages, None)
        return {"type": "http.request", "body": chunk or b"", "more_body": chunk is not None}

    scope: Scope = {"type": "http", "method": "POST", "path": "/alpha", "headers": headers or []}
    asyncio.run(BodyLimit(app, WIDE)(scope, receive, _no_send))
    return seen[0]


async def _no_send(_: Message) -> None:
    raise AssertionError("the app behind the limit answers nothing here")


def test_a_body_at_the_limit_passes() -> None:
    assert len(read_through([bytes(2 * BODY_LIMIT)])) == 2 * BODY_LIMIT


def test_bytes_past_the_limit_are_refused_across_chunks() -> None:
    with pytest.raises(HTTPException) as refused:
        read_through([bytes(BODY_LIMIT), bytes(BODY_LIMIT), b"\0"])

    assert refused.value.status_code == 413


@pytest.mark.parametrize("length", [b"%d" % (2 * BODY_LIMIT + 1), b"1" * 5000])
def test_a_declared_length_is_refused_before_a_byte_is_read(length: bytes) -> None:
    async def app(scope: Scope, receive: Receive, _: Send) -> None:
        await Request(scope, receive).body()

    async def receive() -> Message:
        raise AssertionError("the body was read")

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/alpha",
        "headers": [(b"content-length", length)],
    }

    with pytest.raises(HTTPException) as refused:
        asyncio.run(BodyLimit(app, WIDE)(scope, receive, _no_send))

    assert refused.value.status_code == 413


def test_a_length_that_is_no_number_falls_back_to_counting() -> None:
    assert read_through([b"alpha"], [(b"content-length", "²".encode("latin-1"))]) == b"alpha"


def test_a_socket_passes_untouched() -> None:
    seen: list[Receive] = []

    async def app(_: Scope, receive: Receive, __: Send) -> None:
        seen.append(receive)

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    scope: Scope = {"type": "websocket", "path": "/alpha", "headers": []}
    asyncio.run(BodyLimit(app, {})(scope, receive, _no_send))

    assert seen == [receive]


def test_an_unsigned_large_body_is_refused_by_size(api: TestClient, refused: Any) -> None:
    response = api.post(
        f"/api/v1/agents/{uuid.uuid4()}/heartbeat",
        content=b"{" + b" " * LARGE,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    refused(response, "too-large", "the request body is too large")


def test_a_large_anonymous_registration_is_refused_without_an_echo(api: TestClient) -> None:
    response = api.post(
        "/api/v1/agents/register",
        content=b'{"name": "' + b"a" * LARGE + b'"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert len(response.content) < 100


def test_a_chunked_body_is_cut_at_the_same_limit(api: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b" " * LARGE

    response = api.post(
        "/api/v1/agents/register",
        content=chunks(),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413


def test_a_small_body_still_reaches_its_route(api: TestClient) -> None:
    response = api.post("/api/v1/agents/register", json={"name": "alpha"})

    assert response.status_code == 201
