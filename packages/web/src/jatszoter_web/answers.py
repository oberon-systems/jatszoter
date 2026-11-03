import logging
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

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


def coded(
    request: Request,
    status: int,
    logged: str,
    code: str | None = None,
    error_id: str | None = None,
) -> JSONResponse:
    """A JSON error answer of `web`: the code and an id alone, the rest logged under that id.
    An `error_id` the API already made and logged is passed on as it is."""
    code = code or STATUS_CODES.get(status, "internal-error" if status >= 500 else "refused")
    if error_id is None:
        error_id = str(uuid.uuid4())
        level = logging.ERROR if status >= 500 else logging.WARNING
        where = f"{request.method} {request.url.path}"
        log.log(level, "error %s %s %s %s: %s", error_id, code, status, where, logged)
    return JSONResponse({"error": code, "error_id": error_id}, status_code=status)
