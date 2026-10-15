import hmac
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status
from starlette.datastructures import Headers

from jatszoter_web.cookies import CSRF_COOKIE, new_token
from jatszoter_web.middleware import queue_cookie

HEADER = "x-csrf-token"
FIELD = "csrf_token"


def forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")


def foreign(headers: Headers) -> bool:
    """Whether an `Origin` was sent and names another host than the request's own."""
    origin = headers.get("origin")
    return origin is not None and urlsplit(origin).netloc.lower() != headers.get("host", "").lower()


async def check(request: Request, expected: str | None, *, form: bool = True) -> None:
    """Refuse with 403 unless the request's token matches `expected` and its `Origin` is ours."""
    if foreign(request.headers):
        raise forbidden()
    sent = request.headers.get(HEADER)
    if sent is None and form:
        field = (await request.form()).get(FIELD)
        sent = field if isinstance(field, str) else None
    if not expected or not sent or not hmac.compare_digest(sent.encode(), expected.encode()):
        raise forbidden()


def browser_token(request: Request) -> str:
    """The token of the `jatszoter_csrf` cookie, set first when the browser has none."""
    token = request.cookies.get(CSRF_COOKIE)
    if not token:
        token = new_token()
        queue_cookie(request, CSRF_COOKIE, token)
    return token


async def check_browser(request: Request, *, form: bool = True) -> None:
    await check(request, request.cookies.get(CSRF_COOKIE), form=form)
