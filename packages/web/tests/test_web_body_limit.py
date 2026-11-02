import asyncio
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.types import Message, Receive, Scope, Send

from jatszoter_web.catalog import BUNDLE_LIMIT
from jatszoter_web.cookies import Operator
from jatszoter_web.middleware import BODY_LIMIT, SECURITY_HEADERS, BodyLimit

LARGE = 2 * 1024 * 1024
FORM = {"content-type": "application/x-www-form-urlencoded"}
SignIn = Callable[..., Operator]


async def _no_send(_: Message) -> None:
    raise AssertionError("the app behind the limit answers nothing here")


def run_limit(scope: Scope, receive: Receive) -> list[bytes]:
    seen: list[bytes] = []

    async def app(scope: Scope, receive: Receive, _: Send) -> None:
        seen.append(await Request(scope, receive).body())

    asyncio.run(BodyLimit(app, {("POST", "/alpha"): 2 * BODY_LIMIT})(scope, receive, _no_send))
    return seen


@pytest.mark.parametrize("length", [b"%d" % (BODY_LIMIT + 1), b"1" * 5000])
def test_a_declared_length_is_refused_before_a_byte_is_read(length: bytes) -> None:
    async def receive() -> Message:
        raise AssertionError("the body was read")

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/beta",
        "headers": [(b"content-length", length)],
    }

    with pytest.raises(HTTPException) as refused:
        run_limit(scope, receive)

    assert refused.value.status_code == 413


def test_a_length_that_is_no_number_falls_back_to_counting() -> None:
    sent = iter([b"alpha"])

    async def receive() -> Message:
        chunk = next(sent, None)
        return {"type": "http.request", "body": chunk or b"", "more_body": chunk is not None}

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/alpha",
        "headers": [(b"content-length", "²".encode("latin-1"))],
    }

    assert run_limit(scope, receive) == [b"alpha"]


def test_a_large_login_form_is_refused_by_size_not_by_token(
    client: TestClient, calls: list[httpx.Request]
) -> None:
    response = client.post("/login", content=b"name=" + b"a" * LARGE, headers=FORM)

    assert response.status_code == 413
    assert calls == []


def test_a_chunked_login_form_is_cut_at_the_same_limit(
    client: TestClient, calls: list[httpx.Request]
) -> None:
    def chunks() -> Iterator[bytes]:
        yield b"name=" + b"a" * LARGE

    response = client.post("/login", content=chunks(), headers=FORM)

    assert response.status_code == 413
    assert calls == []


def test_a_large_anonymous_upload_to_login_is_refused(client: TestClient) -> None:
    response = client.post("/login", files={"alpha": ("alpha.bin", bytes(LARGE))})

    assert response.status_code == 413


def test_the_refusal_carries_the_security_headers(client: TestClient) -> None:
    response = client.post("/login", content=b"a" * LARGE, headers=FORM)

    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_a_small_form_with_no_token_is_still_refused_by_token(client: TestClient) -> None:
    response = client.post("/login", data={"name": "alpha", "password": "beta"})

    assert response.status_code == 403


def test_a_bundle_far_over_the_limit_is_refused_before_it_is_read(
    client: TestClient, sign_in: SignIn, calls: list[httpx.Request]
) -> None:
    who = sign_in()

    response = client.post(
        "/scenarios",
        data={"csrf_token": who.csrf},
        files={"bundle": ("alpha.tar.gz", bytes(BUNDLE_LIMIT + 1024 * 1024))},
    )

    assert response.status_code == 413
    assert [call for call in calls if call.method == "POST"] == []
