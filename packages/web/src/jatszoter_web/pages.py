from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from jatszoter_web.api import ApiDown
from jatszoter_web.cookies import SESSION_COOKIE, Operator
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


@dataclass(frozen=True)
class Flash:
    kind: Literal["success", "refusal", "error"]
    title: str
    text: str


def refusal(text: str, status: int) -> Flash:
    return Flash("refusal", "Refused", f"{text} ({status})")


def error(text: str) -> Flash:
    return Flash("error", "Something went wrong", text)


NOT_ALLOWED = Flash("refusal", "Not allowed", "This action is not allowed for your account. (403)")


def render(
    request: Request, name: str, context: dict[str, Any] | None = None, status: int = 200
) -> Response:
    """A page of the shell, never cached; an operator page carries the session's CSRF token."""
    operator: Operator | None = getattr(request.state, "operator", None)
    page = {
        "operator": operator,
        "csrf_token": operator.csrf if operator else None,
        "navigation": NAVIGATION,
        "current": None,
        "flashes": [],
        **(context or {}),
    }
    return templates.TemplateResponse(
        request, name, page, status_code=status, headers={"cache-control": "no-store"}
    )


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
    return render(request, "error.html", {"flashes": [NOT_ALLOWED]}, status=403)


async def api_down(request: Request, exc: Exception) -> Response:
    status = exc.status if isinstance(exc, ApiDown) else 502
    text = exc.message if isinstance(exc, ApiDown) else ""
    return render(request, "error.html", {"flashes": [error(text)]}, status=status)


HANDLERS = {SignedOut: signed_out, NotAllowed: not_allowed, ApiDown: api_down}
