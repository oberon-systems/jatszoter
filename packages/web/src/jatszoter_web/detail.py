import time
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request, WebSocket, status
from fastapi.responses import Response

from jatszoter_web.api import ApiDown, message, signed_headers
from jatszoter_web.cookies import SESSION_COOKIE, unseal_operator
from jatszoter_web.csrf import foreign
from jatszoter_web.pages import (
    Flash,
    Refusal,
    error,
    flash_slot,
    fragment,
    redirect,
    refusal,
    render,
)
from jatszoter_web.relay import bridge, refuse
from jatszoter_web.screens import (
    STOPPABLE,
    TERMINAL,
    by_id,
    came_from,
    described,
    field,
    read,
    refused,
    swapped,
)
from jatszoter_web.session import OperatorDep, PostedOperatorDep, Session, local

router = APIRouter()

LIVE = "deployment-live"
CHECK_BAR = "check-bar"
# The API names the reference it refuses first: `user ...`, or the deployment's live trainee link.
LINK_CONCERNS = {"user": "user_id", "deployment": "user_id"}


def check_refusal(response: httpx.Response) -> Flash:
    """A refused check request, worded as its board words the pending one."""
    text = message(response)
    if text.endswith("has a pending check"):
        text = "a check is already pending"
    return refusal(text, response.status_code)


def turned_down(request: Request, back: str, flash: Flash, status_code: int) -> Response:
    """A refused action: the flash slot for HTMX, the page it came from otherwise."""
    if "hx-request" in request.headers:
        return flash_slot(request, flash, status_code)
    return redirect(request, back, flash)


async def detail(session: Session, deployment_id: uuid.UUID) -> dict[str, Any]:
    """Everything the detail page shows, read from the API."""
    base = f"/api/v1/deployments/{deployment_id}"
    row = await read(session, base)
    users = by_id(await read(session, "/api/v1/users"))
    scenarios = by_id(await read(session, "/api/v1/scenarios"))
    agents = by_id((await read(session, "/api/v1/agents"))["agents"])
    links = sorted(await read(session, f"{base}/links"), key=lambda link: link["created_at"])
    checks = await read(session, f"{base}/checks")
    checks.sort(key=lambda check: check["created_at"], reverse=True)
    trainee = users.get(row["trainee_id"])
    return {
        "current": "/deployments",
        "deployment": row,
        "label": described(row, scenarios, users),
        "scenarios": scenarios,
        "trainee": trainee,
        "agent": agents.get(row["agent_id"] or ""),
        "names": {key: user["name"] for key, user in users.items()},
        "links": links,
        "links_by_id": by_id(links),
        "checks": checks,
        "last": checks[0] if checks else None,
        "recordings": await read(session, f"{base}/recordings"),
        "ready": row["status"] == "READY",
        "ended": row["status"] in TERMINAL,
        "stoppable": row["status"] in STOPPABLE,
        "session_ends": (
            row["ready_at"] + row["session_minutes"] * 60 if row.get("ready_at") else None
        ),
        "trainee_holder": f"trainee {trainee['name']}" if trainee else "trainee",
    }


@router.get("/deployments/{deployment_id:uuid}")
async def deployment(
    request: Request, session: OperatorDep, deployment_id: uuid.UUID, seen: str = ""
) -> Response:
    context = await detail(session, deployment_id)
    if not swapped(request, LIVE):
        return render(request, "deployment.html", context)
    if seen != context["deployment"]["status"]:
        # The terminal opens and closes with the status, so a new status reloads the page.
        return Response(status_code=204, headers={"hx-refresh": "true"})
    return fragment(request, "deployment_live.html", {**context, "oob": True})


@router.post("/deployments/{deployment_id:uuid}/check")
async def request_check(
    request: Request, session: PostedOperatorDep, deployment_id: uuid.UUID
) -> Response:
    back = f"/deployments/{deployment_id}"
    response = await session.call("POST", f"/api/v1/deployments/{deployment_id}/checks")
    if response.status_code != 201:
        return turned_down(request, back, check_refusal(response), response.status_code)
    if "hx-request" not in request.headers:
        return redirect(request, back)
    context = await detail(session, deployment_id)
    return fragment(request, "deployment_check.html", context)


@router.post("/deployments/{deployment_id:uuid}/control")
async def control(
    request: Request, session: PostedOperatorDep, deployment_id: uuid.UUID
) -> Response:
    holder = field(await request.form(), "holder")
    path = f"/api/v1/deployments/{deployment_id}/control"
    response = await session.call("POST", path, {"holder": holder})
    back = f"/deployments/{deployment_id}"
    if response.status_code != 200:
        return turned_down(request, back, refused(response)[0], response.status_code)
    # The terminal learns the new holder from its socket.
    if "hx-request" in request.headers:
        return Response(status_code=204)
    return redirect(request, back)


async def holders(session: Session) -> list[dict[str, Any]]:
    """The users a link can be issued to: the enabled ones, by name."""
    users = await read(session, "/api/v1/users")
    return sorted((row for row in users if row["state"] == "enabled"), key=lambda row: row["name"])


async def known_holders(session: Session) -> list[dict[str, Any]]:
    """The holders, or none when they cannot be read: an issued link is never lost to a read."""
    try:
        return await holders(session)
    except (ApiDown, Refusal):
        return []


def link_dialog_page(request: Request, context: dict[str, Any], status_code: int = 200) -> Response:
    page = {"values": {}, "errors": {}, **context}
    return fragment(request, "link_new.html", page, status_code)


@router.get("/deployments/{deployment_id:uuid}/links")
async def link_dialog(request: Request, session: OperatorDep, deployment_id: uuid.UUID) -> Response:
    return link_dialog_page(request, {"id": deployment_id, "users": await holders(session)})


@router.post("/deployments/{deployment_id:uuid}/links")
async def issue_link(
    request: Request, session: PostedOperatorDep, deployment_id: uuid.UUID
) -> Response:
    user_id = field(await request.form(), "user_id")
    context: dict[str, Any] = {"id": deployment_id, "values": {"user_id": user_id}}
    path = f"/api/v1/deployments/{deployment_id}/links"
    try:
        response = await session.call("POST", path, {"user_id": user_id})
    except ApiDown as down:
        page = {**context, "users": await known_holders(session), "flashes": [error(down.message)]}
        return link_dialog_page(request, page, down.status)
    context["users"] = await known_holders(session)
    if response.status_code != 201:
        flash, errors = refused(response, LINK_CONCERNS)
        page = {**context, "errors": errors, "flashes": [flash]}
        return link_dialog_page(request, page, response.status_code)
    issued = response.json()
    name = next((row["name"] for row in context["users"] if row["id"] == issued["user_id"]), None)
    shown = {
        "name": name or "the user",
        "link": f"{str(request.base_url).rstrip('/')}/s/{issued['token']}",
    }
    return fragment(request, "link_issued.html", shown)


async def link_owner(session: Session, link_id: uuid.UUID, deployment_id: str) -> str:
    """The name of the user a deployment's link belongs to, or a stand-in."""
    try:
        deployment_id = str(uuid.UUID(deployment_id))
    except ValueError:
        return "the user"
    links = await read(session, f"/api/v1/deployments/{deployment_id}/links")
    owner = next((link["user_id"] for link in links if link["id"] == str(link_id)), None)
    users = by_id(await read(session, "/api/v1/users"))
    return str(users[owner]["name"]) if owner in users else "the user"


@router.get("/links/{link_id:uuid}/revoke")
async def revoke_dialog(
    request: Request, session: OperatorDep, link_id: uuid.UUID, deployment_id: str = ""
) -> Response:
    context = {
        "id": link_id,
        "name": await link_owner(session, link_id, deployment_id),
        "next": came_from(request, "/deployments"),
    }
    return fragment(request, "link_revoke.html", context)


@router.post("/links/{link_id:uuid}/revoke")
async def revoke_link(request: Request, session: PostedOperatorDep, link_id: uuid.UUID) -> Response:
    back = local(field(await request.form(), "next")) or "/deployments"
    try:
        response = await session.call("POST", f"/api/v1/links/{link_id}/revoke")
    except ApiDown as down:
        return redirect(request, back, error(down.message))
    if response.status_code != 200:
        return redirect(request, back, refused(response)[0])
    users = by_id(await read(session, "/api/v1/users"))
    owner = users.get(response.json()["user_id"])
    name = owner["name"] if owner else "the user"
    text = f"The link of {name} no longer opens the terminal."
    return redirect(request, back, Flash("success", "Link revoked", text))


@router.websocket("/deployments/{deployment_id:uuid}/attach")
async def operator_socket(websocket: WebSocket, deployment_id: uuid.UUID) -> None:
    """The operator's terminal: the API's attach socket, signed with the session's credential."""
    operator = unseal_operator(websocket.cookies.get(SESSION_COOKIE), websocket.app.state.keys)
    if (
        operator is None
        or operator.expires_at <= int(time.time())
        or "origin" not in websocket.headers
        or foreign(websocket.headers)
    ):
        await refuse(websocket, status.WS_1008_POLICY_VIOLATION)
        return
    path = f"/api/v1/deployments/{deployment_id}/attach"
    # The API sees the path under the base path of its URL, and checks the signature on that.
    prefix = urlsplit(websocket.app.state.settings.api_base).path.rstrip("/")
    await bridge(websocket, path, signed_headers(operator, "GET", prefix + path, b""), None)
