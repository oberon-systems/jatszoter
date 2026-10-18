import json
import time
from dataclasses import asdict, dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from jatszoter_web.api import ApiDown
from jatszoter_web.cookies import (
    FLASH_COOKIE,
    SESSION_COOKIE,
    Operator,
    seal,
    unseal,
    unseal_operator,
)
from jatszoter_web.middleware import queue_cookie
from jatszoter_web.session import NotAllowed, SignedOut, local, login_url

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

NAVIGATION = (
    ("/deployments", "Deployments"),
    ("/scenarios", "Scenarios"),
    ("/agents", "Agents"),
    ("/users", "Users"),
    ("/logs", "Logs"),
    ("/storage", "Storage"),
    ("/results", "Results"),
)

TONES = {
    "PENDING": "neutral",
    "STARTING": "info",
    "BOOTSTRAPPING": "info",
    "READY": "success",
    "STOPPING": "warning",
    "COMPLETED": "neutral",
    "FAILED": "danger",
    "CANCELLED": "neutral",
    "pending": "info",
    "passed": "success",
    "failed": "danger",
    "error": "warning",
    "timeout": "warning",
    "enabled": "success",
    "disabled": "neutral",
}


@dataclass(frozen=True)
class Flash:
    kind: Literal["success", "refusal", "error"]
    title: str
    text: str


def refusal(text: str, status: int) -> Flash:
    return Flash("refusal", "Refused", f"{text} ({status})")


def error(text: str) -> Flash:
    return Flash("error", "Something went wrong", text)


class Refusal(Exception):
    """The API refused a read a page is built from; the page shows `flash` instead."""

    def __init__(self, flash: Flash, status: int) -> None:
        super().__init__(flash.text)
        self.flash = flash
        self.status = status


NOT_ALLOWED = Flash("refusal", "Not allowed", "This action is not allowed for your account. (403)")
EXPIRED = Flash(
    "refusal",
    "Refused",
    "The form has expired or came from another page. "
    "Nothing was changed; reload the page and try again. (403)",
)
SERVER_ERROR = Flash(
    "error",
    "Something went wrong",
    "The page failed on the server. Nothing was changed; try again in a moment. (500)",
)
LANDING = "/deployments"
UNCHANGED = "Nothing was changed"


def when(timestamp: int | float | None, seconds: bool = False) -> str:
    """A UTC time as the boards write it, or `-` when there is none."""
    if timestamp is None:
        return "-"
    return time.strftime(
        "%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M", time.gmtime(timestamp)
    )


def clock(timestamp: int | float) -> str:
    """The UTC time of day, as the last check of a check bar shows it."""
    return time.strftime("%H:%M:%S", time.gmtime(timestamp))


def duration(seconds: int | float) -> str:
    whole = int(seconds)
    return f"{whole // 3600:02d}:{whole // 60 % 60:02d}:{whole % 60:02d}"


def size(count: int | None) -> str:
    if count is None:
        return "-"
    if count < 1024:
        return f"{count} B"
    if count < 1024 * 1024:
        return f"{round(count / 1024)} KiB"
    if count < 1024**3:
        return f"{count / 1024**2:.1f} MiB"
    return f"{count / 1024**3:.1f} GiB"


def counted(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


templates.env.filters.update(when=when, clock=clock, duration=duration, size=size, counted=counted)
templates.env.globals.update(tones=TONES)


def flash_later(request: Request, *flashes: Flash) -> None:
    """Have the next page this browser renders show `flashes`, once."""
    plain = json.dumps([asdict(item) for item in flashes]).encode()
    kid: str = request.app.state.settings.cookie_kid
    queue_cookie(request, FLASH_COOKIE, seal(plain, request.app.state.keys, kid, FLASH_COOKIE))


def flashed(request: Request) -> list[Flash]:
    """The flashes an earlier answer left for this page; reading them clears them."""
    value = request.cookies.get(FLASH_COOKIE)
    if not value:
        return []
    queue_cookie(request, FLASH_COOKIE, None)
    plain = unseal(value, request.app.state.keys, FLASH_COOKIE)
    try:
        return [Flash(**item) for item in json.loads(plain or b"")]
    except (ValueError, TypeError):
        return []


def render(
    request: Request, name: str, context: dict[str, Any] | None = None, status: int = 200
) -> Response:
    """A page of the shell, never cached; an operator page carries the session's CSRF token."""
    operator: Operator | None = getattr(request.state, "operator", None)
    context = context or {}
    page = {
        "operator": operator,
        "csrf_token": operator.csrf if operator else None,
        "navigation": NAVIGATION,
        "current": None,
        **context,
        "flashes": [*flashed(request), *context.get("flashes", [])],
    }
    return templates.TemplateResponse(
        request, name, page, status_code=status, headers={"cache-control": "no-store"}
    )


def fragment(
    request: Request, name: str, context: dict[str, Any] | None = None, status: int = 200
) -> Response:
    """A part of a page HTMX swaps in; it leaves pending flashes to the next page."""
    operator: Operator | None = getattr(request.state, "operator", None)
    page = {"csrf_token": operator.csrf if operator else None, "flashes": [], **(context or {})}
    return templates.TemplateResponse(
        request, name, page, status_code=status, headers={"cache-control": "no-store"}
    )


def redirect(request: Request, url: str, *flashes: Flash) -> Response:
    """303 to `url`, `HX-Redirect` for HTMX; `flashes` show once on the page it lands on."""
    if flashes:
        flash_later(request, *flashes)
    if "hx-request" in request.headers:
        return Response(status_code=204, headers={"hx-redirect": url})
    return RedirectResponse(url, status_code=303)


def flash_slot(request: Request, item: Flash, status: int) -> Response:
    """An HTMX answer that puts `item` into the page's flash slot, whatever was targeted."""
    response = fragment(request, "flash_slot.html", {"flashes": [item]}, status)
    response.headers.update({"hx-retarget": "#flashes", "hx-reswap": "innerHTML"})
    return response


async def signed_out(request: Request, exc: Exception) -> Response:
    refused = isinstance(exc, SignedOut) and exc.refused
    if "hx-request" in request.headers:
        if refused:
            return Response(status_code=401, headers={"hx-refresh": "true"})
        queue_cookie(request, SESSION_COOKIE, None)
        return Response(status_code=401, headers={"hx-redirect": login_url(request)})
    queue_cookie(request, SESSION_COOKIE, None)
    return RedirectResponse(login_url(request), status_code=303)


def signed_in(request: Request) -> Operator | None:
    """The operator of the request, read from the session cookie when no route has read it."""
    operator: Operator | None = getattr(request.state, "operator", None)
    if operator is None:
        operator = unseal_operator(request.cookies.get(SESSION_COOKIE), request.app.state.keys)
        if operator is None or operator.expires_at <= time.time():
            return None
        request.state.operator = operator
    return operator


def again(request: Request) -> str | None:
    """The page to reload: this one for a GET, else the page of ours the form was sent from."""
    if request.method == "GET":
        url = request.url
        return local(url.path + (f"?{url.query}" if url.query else ""))
    referer = urlsplit(request.headers.get("referer", ""))
    if referer.netloc.lower() != request.headers.get("host", "").lower():
        return None
    return local(referer.path + (f"?{referer.query}" if referer.query else ""))


def failure(
    request: Request,
    status: int,
    title: str,
    subtitle: str,
    item: Flash,
    reload: bool = False,
) -> Response:
    """An error page: the operator shell with its actions, or the signed-out card."""
    if "hx-request" in request.headers:
        return flash_slot(request, item, status)
    signed_in(request)
    context: dict[str, Any] = {
        "title": title,
        "subtitle": subtitle,
        "flashes": [item],
        "landing": LANDING,
    }
    if reload:
        context["again"] = again(request)
    return render(request, "error.html", context, status=status)


def not_found(request: Request) -> Response:
    where = "start from the deployments" if signed_in(request) else "log in"
    text = f"There is no page at this address. Check the link or {where}. (404)"
    item = Flash("refusal", "Not found", text)
    return failure(request, 404, "Page not found", "Nothing lives at this address", item)


def refused(request: Request, status: int, reason: str) -> Response:
    text = f"This request is not accepted: {reason}. Nothing was changed. ({status})"
    return failure(request, status, "Request refused", UNCHANGED, Flash("refusal", "Refused", text))


def server_error(request: Request) -> Response:
    return failure(request, 500, "Something went wrong", UNCHANGED, SERVER_ERROR, reload=True)


def failed_scope(scope: Scope) -> Response:
    """The server error page for a request that raised before it was answered."""
    return server_error(Request(scope))


async def http_error(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, StarletteHTTPException) or request.url.path.startswith("/api/"):
        return await http_exception_handler(request, cast(StarletteHTTPException, exc))
    status = exc.status_code
    if status == 403:
        return failure(request, 403, "Request refused", UNCHANGED, EXPIRED, reload=True)
    if status == 404:
        return not_found(request)
    if status >= 500:
        return server_error(request)
    try:
        reason = HTTPStatus(status).phrase.lower()
    except ValueError:
        reason = "the request is not valid"
    return refused(request, status, reason)


async def invalid_request(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, RequestValidationError) or request.url.path.startswith("/api/"):
        return await request_validation_exception_handler(
            request, cast(RequestValidationError, exc)
        )
    return refused(request, 422, "the request is not valid")


async def not_allowed(request: Request, _: Exception) -> Response:
    return failure(request, 403, "Not allowed", "Your account cannot open this", NOT_ALLOWED)


async def refused_read(request: Request, exc: Exception) -> Response:
    item, status = (exc.flash, exc.status) if isinstance(exc, Refusal) else (NOT_ALLOWED, 403)
    if "hx-request" in request.headers:
        return flash_slot(request, item, status)
    return render(request, "error.html", {"flashes": [item]}, status=status)


async def api_down(request: Request, exc: Exception) -> Response:
    status = exc.status if isinstance(exc, ApiDown) else 502
    text = exc.message if isinstance(exc, ApiDown) else ""
    if "hx-request" in request.headers:
        return flash_slot(request, error(text), status)
    return render(request, "error.html", {"flashes": [error(text)]}, status=status)


HANDLERS = {
    SignedOut: signed_out,
    NotAllowed: not_allowed,
    ApiDown: api_down,
    Refusal: refused_read,
    StarletteHTTPException: http_error,
    RequestValidationError: invalid_request,
}
