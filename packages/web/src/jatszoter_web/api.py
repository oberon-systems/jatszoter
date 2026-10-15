import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from jatszoter_web.cookies import Operator

TIMEOUT_SECONDS = 30


class ApiDown(Exception):
    """The API is unreachable, too slow or failed; `status` is what `web` answers."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


UNREACHABLE = "The API cannot be reached. Nothing was changed; try again in a moment. (502)"
TIMED_OUT = "The API did not answer in time. Try again in a moment. (504)"


def failed(code: int) -> ApiDown:
    return ApiDown(502, f"The API failed to answer. Try again in a moment. ({code})")


def signature(key: str, method: str, path: str, body: bytes, timestamp: int) -> str:
    """HMAC-SHA256 of `ts\\nMETHOD\\npath\\nsha256(body)`, the way the API checks it."""
    message = "\n".join((str(timestamp), method.upper(), path, hashlib.sha256(body).hexdigest()))
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def signed_headers(operator: Operator, method: str, path: str, body: bytes) -> dict[str, str]:
    timestamp = int(time.time())
    return {
        "x-owner": str(operator.user_id),
        "x-credential": operator.credential,
        "x-timestamp": str(timestamp),
        "x-signature": signature(operator.key, method, path, body, timestamp),
    }


def message(response: httpx.Response) -> str:
    """The API's own message for a refusal, or a stand-in when it sent none."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return detail if isinstance(detail, str) else "the request was refused"


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
        except httpx.TimeoutException:
            raise ApiDown(504, TIMED_OUT) from None
        except httpx.TransportError:
            raise ApiDown(502, UNREACHABLE) from None

    async def anonymous(self, method: str, path: str, payload: Any = None) -> httpx.Response:
        body = b"" if payload is None else json.dumps(payload).encode()
        headers = {"content-type": "application/json"} if payload is not None else {}
        return await self.send(self.http.build_request(method, path, content=body, headers=headers))

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
        request.headers.update(signed_headers(operator, method, request.url.path, body))
        return await self.send(request)

    async def aclose(self) -> None:
        await self.http.aclose()
