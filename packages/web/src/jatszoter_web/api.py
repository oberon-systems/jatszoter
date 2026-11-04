import hashlib
import hmac
import json
import secrets
import time
from typing import Any

import httpx

from jatszoter_web.answers import STATUS_CODES, logged_error
from jatszoter_web.cookies import Operator
from jatszoter_web.texts import answered

TIMEOUT_SECONDS = 30


class ApiDown(Exception):
    """The API is unreachable, too slow or failed; `status` is what `web` answers, `message`
    what a page shows, with the `error_id` the log holds the cause under."""

    def __init__(self, status: int, text: str, error_id: str, code: int | None = None) -> None:
        self.status = status
        self.error_id = error_id
        self.message = f"{text} (error {error_id}) ({code or status})"
        super().__init__(self.message)


UNREACHABLE = "The API cannot be reached. Nothing was changed; try again in a moment."
TIMED_OUT = "The API did not answer in time. Try again in a moment."
FAILED = "The API failed to answer. Try again in a moment."


def unanswered(status: int, text: str, request: httpx.Request, exc: Exception) -> ApiDown:
    code = STATUS_CODES[status]
    error_id = logged_error(status, code, request.method, request.url.path, repr(exc))
    return ApiDown(status, text, error_id)


def failed(response: httpx.Response) -> ApiDown:
    """An answer of the API no page can use, under the id the API logged it with, if it has one."""
    code = response.status_code
    request = response.request
    error_id = answered(response)[1] or logged_error(
        502, "api-failed", request.method, request.url.path, f"the API answered {code}"
    )
    return ApiDown(502, FAILED, error_id, code)


def signature(key: str, method: str, path: str, body: bytes, timestamp: int, nonce: str) -> str:
    """HMAC-SHA256 of `ts\\nnonce\\nMETHOD\\npath[?query]\\nsha256(body)`, the way the API
    checks it."""
    digest = hashlib.sha256(body).hexdigest()
    message = "\n".join((str(timestamp), nonce, method.upper(), path, digest))
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def signed_headers(operator: Operator, method: str, path: str, body: bytes) -> dict[str, str]:
    timestamp = int(time.time())
    nonce = secrets.token_hex(16)
    return {
        "x-owner": str(operator.user_id),
        "x-credential": operator.credential,
        "x-timestamp": str(timestamp),
        "x-nonce": nonce,
        "x-signature": signature(operator.key, method, path, body, timestamp, nonce),
    }


class Api:
    """The HTTP client `web` reaches the API with; it never follows a redirect."""

    def __init__(self, base: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base = base
        self.http = httpx.AsyncClient(
            base_url=base, timeout=TIMEOUT_SECONDS, transport=transport, follow_redirects=False
        )

    async def send(self, request: httpx.Request) -> httpx.Response:
        try:
            return await self.http.send(request)
        except httpx.TimeoutException as exc:
            raise unanswered(504, TIMED_OUT, request, exc) from None
        except httpx.TransportError as exc:
            raise unanswered(502, UNREACHABLE, request, exc) from None

    async def anonymous(
        self,
        method: str,
        path: str,
        payload: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        body = b"" if payload is None else json.dumps(payload).encode()
        sent = {"content-type": "application/json"} if payload is not None else {}
        sent.update(headers or {})
        return await self.send(self.http.build_request(method, path, content=body, headers=sent))

    async def signed(
        self,
        operator: Operator,
        method: str,
        path: str,
        payload: Any = None,
        params: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes]] | None = None,
    ) -> httpx.Response:
        """A call signed for `operator`, with a JSON `payload` or a multipart body of `files`."""
        if files is not None:
            request = self.http.build_request(method, path, files=files, params=params)
            body = request.read()
        else:
            body = b"" if payload is None else json.dumps(payload).encode()
            request = self.http.build_request(method, path, content=body, params=params)
            if payload is not None:
                request.headers["content-type"] = "application/json"
        target = request.url.path
        if request.url.query:
            target += "?" + request.url.query.decode("ascii")
        request.headers.update(signed_headers(operator, method, target, body))
        return await self.send(request)

    async def aclose(self) -> None:
        await self.http.aclose()
