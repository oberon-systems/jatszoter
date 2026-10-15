import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from jatszoter_web.api import ApiDown
from jatszoter_web.cookies import FLASH_COOKIE, SESSION_COOKIE, Operator, seal, unseal
from jatszoter_web.middleware import queue_cookie
from jatszoter_web.session import NotAllowed, SignedOut, login_url

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


def when(timestamp: int | float | None, seconds: bool = False) -> str:
    """A UTC time as the boards write it, or `-` when there is none."""
    if timestamp is None:
        return "-"
    return time.strftime(
        "%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M", time.gmtime(timestamp)
    )


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


templates.env.filters.update(when=when, size=size, counted=counted)
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


async def not_allowed(request: Request, _: Exception) -> Response:
    if "hx-request" in request.headers:
        return flash_slot(request, NOT_ALLOWED, 403)
    return render(request, "error.html", {"flashes": [NOT_ALLOWED]}, status=403)


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
}
