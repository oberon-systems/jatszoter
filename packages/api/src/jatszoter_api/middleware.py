from collections.abc import Mapping

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

BODY_LIMIT = 64 * 1024
MAX_LENGTH_DIGITS = 18
ANSWER_HEADERS = {"cache-control": "no-store", "x-content-type-options": "nosniff"}


def _too_large() -> HTTPException:
    return HTTPException(status_code=413, detail="the request body is too large")


class BodyLimit:
    """Refuses a body past its limit: the declared length first, then the bytes as they arrive."""

    def __init__(self, app: ASGIApp, wider: Mapping[tuple[str, str], int]) -> None:
        self.app = app
        self.wider = wider

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.wider.get((scope["method"], scope["path"]), BODY_LIMIT)
        declared = Headers(scope=scope).get("content-length", "")
        # isdigit alone passes a superscript, and int() refuses a number thousands of digits long.
        over = (
            declared.isascii()
            and declared.isdigit()
            and (len(declared) > MAX_LENGTH_DIGITS or int(declared) > limit)
        )
        received = 0

        # Raised where a route reads the body, so the app's handlers shape the answer.
        async def bounded() -> Message:
            nonlocal received
            if over:
                raise _too_large()
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _too_large()
            return message

        await self.app(scope, bounded, send)


class AnswerHeaders:
    """Keeps every answer out of a cache and a browser from guessing its type."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def headed(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in ANSWER_HEADERS.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, headed)
