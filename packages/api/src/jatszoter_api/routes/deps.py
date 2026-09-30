from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from sqlmodel import Session

from jatszoter_api.db import get_session
from jatszoter_api.errors import (
    ForbiddenError,
    InvalidPasswordError,
    InvalidTransitionError,
    NotAuthorizedError,
    NotFoundError,
    ThrottledError,
)

SessionDep = Annotated[Session, Depends(get_session)]

_ERROR_STATUS: dict[type[Exception], int] = {
    NotAuthorizedError: 401,
    ForbiddenError: 403,
    NotFoundError: 404,
    InvalidTransitionError: 409,
    InvalidPasswordError: 422,
    ThrottledError: 429,
}


def domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=_ERROR_STATUS.get(type(exc), 400))
