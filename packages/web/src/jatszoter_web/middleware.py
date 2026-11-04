import logging
import time
from collections.abc import Callable, Mapping

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import HTTPConnection
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jatszoter_web.answers import logged_error, redacted
from jatszoter_web.cookies import set_cookie_header

SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
        "frame-ancestors 'none'"
    ),
    "referrer-policy": "same-origin",
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
}

# asciinema-player builds its terminal from WebAssembly; the replay page alone allows it.
WASM_POLICY = SECURITY_HEADERS["content-security-policy"].replace(
    "script-src 'self'", "script-src 'self' 'wasm-unsafe-eval'"
)

COOKIES_STATE = "set_cookies"
POLICY_STATE = "content_security_policy"
BODY_LIMIT = 64 * 1024
MAX_LENGTH_DIGITS = 18

access_log = logging.getLogger("jatszoter_web.access")


def queue_cookie(connection: HTTPConnection, name: str, value: str | None) -> None:
    """Have the answer set the cookie `name` to `value`, or clear it when `value` is None."""
    state = connection.scope.setdefault("state", {})
    state.setdefault(COOKIES_STATE, {})[name] = value


def allow_wasm(connection: HTTPConnection) -> None:
    """Have the answer carry the policy that lets a page compile WebAssembly."""
    connection.scope.setdefault("state", {})[POLICY_STATE] = WASM_POLICY


class DropUvicornAccess(logging.Filter):
    """uvicorn's access line carries the query and the client; `web` writes its own instead."""

    def filter(self, record: logging.LogRecord) -> bool:
        return False


def _too_large() -> HTTPException:
    return HTTPException(status_code=413, detail="too large")


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


class Guard:
    """Security headers and queued cookies on every answer, and the access log line."""

    def __init__(
        self, app: ASGIApp, failure: Callable[[Scope, str], Response] | None = None
    ) -> None:
        self.app = app
        self.failure = failure

    def failed(self, scope: Scope, error_id: str) -> Response:
        """The page for a request that raised, plain text when even that page fails."""
        if self.failure is not None:
            try:
                return self.failure(scope, error_id)
            except Exception:
                access_log.exception("the server error page failed for error %s", error_id)
        return PlainTextResponse(f"Internal Server Error (error {error_id})", 500)

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
                state = scope.get("state", {})
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                if POLICY_STATE in state:
                    headers["content-security-policy"] = state[POLICY_STATE]
                cookies = state.get(COOKIES_STATE, {})
                for name, cookie in cookies.items():
                    headers.append("set-cookie", set_cookie_header(name, cookie))
            await send(message)

        try:
            await self.app(scope, receive, guarded)
        except Exception as exc:
            error_id = logged_error(
                500, "internal-error", scope["method"], scope["path"], "the request raised", exc
            )
            # Raised only to cut an answer already started; uvicorn would log it a second time.
            if status is not None:
                raise
            await self.failed(scope, error_id)(scope, receive, guarded)
        finally:
            access_log.info(
                "%s %s %d %.0fms",
                scope["method"],
                redacted(scope["path"]),
                status or 500,
                (time.monotonic() - started) * 1000,
            )
