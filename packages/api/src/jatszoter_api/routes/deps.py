import ipaddress
from typing import Annotated

import httpx
from fastapi import Depends, Request
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
from jatszoter_api.settings import Settings, app_settings

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
