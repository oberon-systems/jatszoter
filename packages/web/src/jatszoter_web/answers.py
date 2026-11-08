import logging
import re
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

LINK_PATH = re.compile(r"/s/[^/?#\s]+")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")

STATUS_CODES = {
    401: "not-authorized",
    403: "forbidden",
    404: "not-found",
    405: "method-not-allowed",
    413: "too-large",
    422: "invalid-request",
    429: "throttled",
    502: "api-unreachable",
    504: "api-timeout",
}


def redacted(path: str) -> str:
    """`path` fit for a log line: no link token, and control characters escaped."""
    hidden = LINK_PATH.sub("/s/<redacted>", path)
    return CONTROL.sub(lambda char: f"\\x{ord(char.group()):02x}", hidden)


def logged_error(
    status: int,
    code: str,
    method: str,
    path: str,
    message: str,
    exc: BaseException | None = None,
) -> str:
    """Log the real error under a fresh id; the id is all a browser is shown of it."""
    error_id = str(uuid.uuid4())
    level = logging.ERROR if status >= 500 else logging.WARNING
    where = f"{method} {redacted(path)}"
    log.log(level, "error %s %s %s %s: %s", error_id, code, status, where, message, exc_info=exc)
    return error_id


def coded(
    request: Request,
    status: int,
    logged: str,
    code: str | None = None,
    error_id: str | None = None,
) -> JSONResponse:
    """A JSON error answer of `web`: the code and an id alone, the rest logged under that id.
    An `error_id` already logged, by the API or by `web`, is passed on as it is."""
    code = code or STATUS_CODES.get(status, "internal-error" if status >= 500 else "refused")
    if error_id is None:
        error_id = logged_error(status, code, request.method, request.url.path, logged)
    return JSONResponse({"error": code, "error_id": error_id}, status_code=status)
