import ipaddress
import logging
import uuid
from collections.abc import Callable
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, Field
from sqlmodel import Session

from jatszoter_api.db import get_session
from jatszoter_api.errors import (
    ConflictError,
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
from jatszoter_api.models import Check, Recording
from jatszoter_api.models.recording import live
from jatszoter_api.settings import Settings, app_settings

log = logging.getLogger(__name__)

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(app_settings)]

_ERROR_STATUS: dict[type[Exception], int] = {
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


def domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=_ERROR_STATUS.get(type(exc), 400))


def recorded(
    app: FastAPI, deployment_id: uuid.UUID, write: Callable[..., None], *args: Any
) -> None:
    """Hand the deployment's live recording to `write`; a failure is logged and goes no further."""
    try:
        recording_id = live(deployment_id)
        if recording_id is None:
            return
        with Session(app.state.engine) as session:
            write(Recording(session=session).load(id=recording_id), *args)
    except Exception as error:
        log.error("recording of deployment %s not written: %s", deployment_id, type(error).__name__)


def _marks(request: Request, settings: SettingsDep) -> Callable[[Check], None]:
    """What writes a settled check into its deployment's live recording as a marker."""

    def mark(check: Check) -> None:
        recorded(
            request.app,
            check.deployment_id,
            Recording.mark,
            check.marker,
            None if settings.seaweed_url is None else str(settings.seaweed_url),
            settings.recording_limit_bytes,
        )

    return mark


MarkDep = Annotated[Callable[[Check], None], Depends(_marks)]


def _parsed(url: str) -> httpx.URL:
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        raise ValueError("not a URL") from None
    if parsed.scheme not in ("http", "https") or not parsed.host:
        raise ValueError("only http and https URLs with a host")
    return parsed


def _web(url: str) -> str:
    _parsed(url)
    return url


def _fetchable(url: str) -> str:
    """`https`, or `http` to a loopback address: the only URLs the API fetches itself."""
    parsed = _parsed(url)
    if parsed.scheme == "https":
        return url
    try:
        if ipaddress.ip_address(parsed.host).is_loopback:
            return url
    except ValueError:
        pass
    raise ValueError("only https, or http to a loopback address")


WebUrl = Annotated[str, Field(max_length=2048), AfterValidator(_web)]
FetchUrl = Annotated[str, Field(max_length=2048), AfterValidator(_fetchable)]
