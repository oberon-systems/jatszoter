import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from jatszoter_web.api import ApiDown
from jatszoter_web.pages import Flash, error, fragment, redirect, render
from jatszoter_web.screens import came_from, field, read, refused, swapped
from jatszoter_web.session import OperatorDep, PostedOperatorDep, Session, local

router = APIRouter()

TABLES = "agents-tables"


@router.get("/agents")
async def agents(request: Request, session: OperatorDep) -> Response:
    context: dict[str, Any] = {"current": "/agents"}
    try:
        context.update(await read(session, "/api/v1/agents"))
        code = 200
    except ApiDown as down:
        context.update(registrations=[], agents=[], failure=error(down.message))
        code = down.status
    if swapped(request, TABLES):
        return fragment(request, "agents_tables.html", {**context, "swapped": True}, code)
    flashes = [context["failure"]] if "failure" in context else []
    return render(request, "agents.html", {**context, "flashes": flashes}, code)


async def known(session: Session, agent_id: uuid.UUID) -> tuple[str, bool]:
    """The agent's name, and whether it is a registration that waits for approval."""
    listed = await read(session, "/api/v1/agents")
    for row in listed["registrations"]:
        if row["id"] == str(agent_id):
            return row["name"], True
    for row in listed["agents"]:
        if row["id"] == str(agent_id):
            return row["name"], False
    return "the agent", False


@router.get("/agents/{agent_id:uuid}/state")
async def agent_dialog(request: Request, session: OperatorDep, agent_id: uuid.UUID) -> Response:
    name, _ = await known(session, agent_id)
    context = {"id": agent_id, "name": name, "next": came_from(request, "/agents")}
    return fragment(request, "agent_disable.html", context)


@router.post("/agents/{agent_id:uuid}/state")
async def switch_agent(
    request: Request, session: PostedOperatorDep, agent_id: uuid.UUID
) -> Response:
    form = await request.form()
    back = local(field(form, "next")) or "/agents"
    state = field(form, "state")
    name, pending = await known(session, agent_id)
    response = await session.call("POST", f"/api/v1/agents/{agent_id}/state", {"state": state})
    if response.status_code != 200:
        return redirect(request, back, refused(response)[0])
    if pending:
        text = f"{name} is enabled and takes deployments from its next heartbeat."
        flash = Flash("success", "Agent approved", text)
    elif state == "enabled":
        flash = Flash("success", "Agent enabled", f"{name} takes deployments again.")
    else:
        text = f"{name} takes no deployments until it is enabled again."
        flash = Flash("success", "Agent disabled", text)
    return redirect(request, back, flash)
