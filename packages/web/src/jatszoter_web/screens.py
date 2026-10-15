from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import Request
from starlette.datastructures import FormData

from jatszoter_web.api import failed, message
from jatszoter_web.pages import Flash, Refusal, refusal
from jatszoter_web.session import Session, local

STATUSES = (
    "PENDING",
    "STARTING",
    "BOOTSTRAPPING",
    "READY",
    "STOPPING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
)
STOPPABLE = ("PENDING", "STARTING", "BOOTSTRAPPING", "READY")
TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")
ROLES = ("operator", "trainee", "viewer")

Row = dict[str, Any]


async def read(session: Session, path: str, params: dict[str, str] | None = None) -> Any:
    """What the API answers a read with; a 4xx is a refusal, any other answer but 200 a failure."""
    response = await session.call("GET", path, params=params)
    if 400 <= response.status_code < 500:
        raise Refusal(refused(response)[0], response.status_code)
    if response.status_code != 200:
        raise failed(response.status_code)
    try:
        return response.json()
    except ValueError:
        raise failed(response.status_code) from None


def by_id(rows: list[Row]) -> dict[str, Row]:
    return {row["id"]: row for row in rows}


def field(form: FormData, name: str) -> str:
    value = form.get(name)
    return value.strip() if isinstance(value, str) else ""


def swapped(request: Request, target: str) -> bool:
    """Whether HTMX asks for the element `target` alone rather than for the page."""
    headers = request.headers
    return headers.get("hx-target") == target and "hx-history-restore-request" not in headers


def came_from(request: Request, fallback: str) -> str:
    """The local page an HTMX request was made from, to return to after a dialog."""
    current = urlsplit(request.headers.get("hx-current-url", ""))
    if current.netloc.lower() != request.headers.get("host", "").lower():
        return fallback
    return local(current.path + (f"?{current.query}" if current.query else "")) or fallback


def refused(
    response: httpx.Response, concerns: dict[str, str] | None = None
) -> tuple[Flash, dict[str, str]]:
    """The flash of an API refusal, and its message by field: named, or by a `concerns` prefix."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    errors: dict[str, str] = {}
    if isinstance(detail, list):
        for item in detail:
            if isinstance(item, dict) and item.get("loc"):
                errors.setdefault(str(item["loc"][-1]), str(item.get("msg", "")))
        text = next(iter(errors.values()), "the request was refused")
    else:
        text = message(response)
        for prefix, name in (concerns or {}).items():
            if text.startswith(prefix):
                errors[name] = text
                break
    return refusal(text, response.status_code), errors


def described(deployment: Row, scenarios: dict[str, Row], users: dict[str, Row]) -> str:
    """`web-basics v3 for alpha`, the way flashes and dialogs name a deployment."""
    scenario = scenarios.get(deployment["scenario_id"])
    user = users.get(deployment.get("trainee_id") or "")
    what = f"{scenario['slug']} v{scenario['version']}" if scenario else "the deployment"
    return f"{what} for {user['name']}" if user else what
