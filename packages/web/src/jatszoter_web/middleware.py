import logging
import re
import time

from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jatszoter_web.cookies import set_cookie_header

SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
        "frame-ancestors 'none'"
    ),
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
}

LINK_PATH = re.compile(r"/s/[^/?#\s]+")
COOKIES_STATE = "set_cookies"

access_log = logging.getLogger("jatszoter_web.access")


def redacted(path: str) -> str:
    return LINK_PATH.sub("/s/<redacted>", path)


def queue_cookie(connection: HTTPConnection, name: str, value: str | None) -> None:
    """Have the answer set the cookie `name` to `value`, or clear it when `value` is None."""
    state = connection.scope.setdefault("state", {})
    state.setdefault(COOKIES_STATE, {})[name] = value


class DropUvicornAccess(logging.Filter):
    """uvicorn's access line carries the query and the client; `web` writes its own instead."""

    def filter(self, record: logging.LogRecord) -> bool:
        return False


class Guard:
    """Security headers and queued cookies on every answer, and the access log line."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        status: int | None = None

        async def guarded(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                cookies = scope.get("state", {}).get(COOKIES_STATE, {})
                for name, cookie in cookies.items():
                    headers.append("set-cookie", set_cookie_header(name, cookie))
            await send(message)

        try:
            await self.app(scope, receive, guarded)
        except Exception:
            if status is None:
                await PlainTextResponse("Internal Server Error", 500)(scope, receive, guarded)
            raise
        finally:
            access_log.info(
                "%s %s %d %.0fms",
                scope["method"],
                redacted(scope["path"]),
                status or 500,
                (time.monotonic() - started) * 1000,
            )
