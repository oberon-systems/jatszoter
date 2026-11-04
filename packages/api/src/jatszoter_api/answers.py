import logging
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi.exceptions import (
    RequestValidationError,
    ValidationException,
    WebSocketRequestValidationError,
)
from fastapi.responses import JSONResponse
from starlette import status
from starlette.exceptions import HTTPException
from starlette.requests import HTTPConnection
from starlette.responses import Response
from starlette.websockets import WebSocket

from jatszoter_api.errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    InvalidBundleError,
    InvalidPasswordError,
    InvalidReferenceError,
    InvalidTransitionError,
    NotAuthorizedError,
    NotFoundError,
    ThrottledError,
    TooLargeError,
)
from jatszoter_api.middleware import ANSWER_HEADERS

LINK_PATH = re.compile(r"/s/[^/?#\s]+")
FIELD = re.compile(r"[a-z][a-z0-9_]{0,63}")

log = logging.getLogger(__name__)

_DOMAIN_STATUS: dict[type[DomainError], int] = {
    NotAuthorizedError: 401,
    ForbiddenError: 403,
    NotFoundError: 404,
    ConflictError: 409,
    InvalidTransitionError: 409,
    TooLargeError: 413,
    InvalidBundleError: 422,
    InvalidPasswordError: 422,
    InvalidReferenceError: 422,
    ThrottledError: 429,
}

_HTTP_CODES = {
    401: "not-authorized",
    403: "forbidden",
    404: "not-found",
    405: "method-not-allowed",
    413: "too-large",
    429: "throttled",
}


def redacted(path: str) -> str:
    return LINK_PATH.sub("/s/<redacted>", path)


async def answer(
    conn: HTTPConnection,
    status_code: int,
    code: str,
    message: str,
    headers: Mapping[str, str] | None = None,
) -> Response | None:
    """Log the real error under a fresh id; the caller is told the code and the id alone."""
    error_id = str(uuid.uuid4())
    method = conn.scope.get("method", "WS")
    where = redacted(conn.url.path)
    level = logging.ERROR if status_code >= 500 else logging.WARNING
    log.log(level, "error %s %s %s %s %s: %s", error_id, code, status_code, method, where, message)
    if isinstance(conn, WebSocket):
        await conn.close(
            status.WS_1011_INTERNAL_ERROR if status_code >= 500 else status.WS_1008_POLICY_VIOLATION
        )
        return None
    body = {"error": code, "error_id": error_id}
    return JSONResponse(body, status_code=status_code, headers=headers)


async def domain_error(conn: HTTPConnection, exc: Exception) -> Response | None:
    if not isinstance(exc, DomainError):
        return await server_error(conn, exc)
    return await answer(conn, _DOMAIN_STATUS.get(type(exc), 400), exc.code, str(exc))


async def http_error(conn: HTTPConnection, exc: Exception) -> Response | None:
    if not isinstance(exc, HTTPException):
        return await server_error(conn, exc)
    code = _HTTP_CODES.get(exc.status_code, "refused")
    return await answer(conn, exc.status_code, code, str(exc.detail), exc.headers)


def field_code(errors: Sequence[Any]) -> str:
    """`<field>-missing` or `<field>-invalid` for the first error, `body-invalid` for bad JSON."""
    first = errors[0] if errors and isinstance(errors[0], Mapping) else {}
    loc = list(first.get("loc", ()))
    if first.get("type") == "json_invalid":
        return "body-invalid"
    # Only the first level is our schema's; a deeper key may be one the caller chose.
    name = loc[1] if len(loc) > 1 else loc[0] if loc else None
    if not isinstance(name, str) or not FIELD.fullmatch(name):
        return "invalid-request"
    field = name.replace("_", "-")
    return f"{field}-missing" if first.get("type") == "missing" else f"{field}-invalid"


async def invalid_request(conn: HTTPConnection, exc: Exception) -> Response | None:
    errors = exc.errors() if isinstance(exc, ValidationException) else []
    logged = [(error.get("loc"), error.get("type"), error.get("msg")) for error in errors]
    return await answer(conn, 422, field_code(errors), repr(logged))


async def server_error(conn: HTTPConnection, exc: Exception) -> Response:
    error_id = str(uuid.uuid4())
    method = conn.scope.get("method", "WS")
    where = redacted(conn.url.path)
    log.error("error %s internal-error 500 %s %s", error_id, method, where, exc_info=exc)
    body = {"error": "internal-error", "error_id": error_id}
    # Starlette runs the handler of `Exception` outside every middleware.
    return JSONResponse(body, status_code=500, headers=ANSWER_HEADERS)


HANDLERS: dict[type[Exception], Any] = {
    DomainError: domain_error,
    HTTPException: http_error,
    RequestValidationError: invalid_request,
    WebSocketRequestValidationError: invalid_request,
    Exception: server_error,
}
