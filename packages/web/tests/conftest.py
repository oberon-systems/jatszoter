import hashlib
import hmac
import time
import uuid
from collections.abc import Callable

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jatszoter_web.app import create_app
from jatszoter_web.cookies import CSRF_COOKIE, SESSION_COOKIE, Operator, seal_operator
from jatszoter_web.settings import Settings, b64url_encode

HOST = "web.example.com"
KID = "k1"

Routes = dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]]


@pytest.fixture
def settings() -> Settings:
    return Settings(
        api_url="http://api.example.com",
        cookie_keys={KID: b64url_encode(bytes(range(32)))},
        cookie_kid=KID,
    )


@pytest.fixture
def routes() -> Routes:
    """What the fake API answers, by method and path; anything else gets 404."""
    return {}


@pytest.fixture
def calls() -> list[httpx.Request]:
    """Every request the fake API got."""
    return []


@pytest.fixture
def app(settings: Settings, routes: Routes, calls: list[httpx.Request]) -> FastAPI:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        handler = routes.get((request.method, request.url.path))
        if handler is None:
            return httpx.Response(404, json={"detail": "not found"})
        return handler(request)

    return create_app(settings, httpx.MockTransport(handle))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url=f"https://{HOST}", follow_redirects=False)


@pytest.fixture
def sign_in(app: FastAPI, client: TestClient) -> Callable[..., Operator]:
    """Put a session cookie into the client, as a login would have."""

    def put(expires_in: int = 3600, key: str = "signing-key-alpha") -> Operator:
        expires_at = int(time.time()) + expires_in
        who = Operator(
            user_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
            name="alpha",
            credential=f"m1:1:{expires_at}",
            expires_at=expires_at,
            key=key,
            csrf="csrf-token-alpha",
        )
        client.cookies.set(SESSION_COOKIE, seal_operator(who, app.state.keys, KID), domain=HOST)
        return who

    return put


@pytest.fixture
def browser_csrf(client: TestClient) -> str:
    """The `jatszoter_csrf` cookie a login or trainee page would have set."""
    token = "csrf-token-browser"
    client.cookies.set(CSRF_COOKIE, token, domain=HOST)
    return token


@pytest.fixture
def signed_by() -> Callable[[httpx.Request, str], bool]:
    """Whether a request carries the signature the API would accept for a key."""

    def check(request: httpx.Request, key: str) -> bool:
        body = hashlib.sha256(request.content).hexdigest()
        text = "\n".join(
            (request.headers.get("x-timestamp", ""), request.method, request.url.path, body)
        )
        expected = hmac.new(key.encode(), text.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, request.headers.get("x-signature", ""))

    return check
