import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from jatszoter_web.api import ApiDown
from jatszoter_web.pages import Flash, Refusal, error, fragment, redirect, render
from jatszoter_web.screens import (
    STATUSES,
    STOPPABLE,
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

TABLE = "deployments-table"
# The API names the reference it refuses first: `agent ...`, `user ...`, `scenario ...`.
CONCERNS = {
    "scenario": "scenario_id",
    "image": "scenario_id",
    "user": "trainee_id",
    "agent": "agent_id",
}


async def names(session: Session) -> dict[str, Any]:
    """The scenarios, users and agents a deployment's ids are shown by."""
    agents = await read(session, "/api/v1/agents")
    return {
        "scenarios": by_id(await read(session, "/api/v1/scenarios")),
        "users": by_id(await read(session, "/api/v1/users")),
        "agents": by_id(agents["agents"]),
    }


@router.get("/deployments")
async def deployments(request: Request, session: OperatorDep, status: str = "") -> Response:
    status = status if status in STATUSES else ""
    context: dict[str, Any] = {
        "current": "/deployments",
        "status": status,
        "statuses": STATUSES,
        "stoppable": STOPPABLE,
    }
    try:
        params = {"status": status} if status else None
        context["rows"] = await read(session, "/api/v1/deployments", params)
        context.update(await names(session))
        code = 200
    except ApiDown as down:
        context.update(failure=error(down.message), rows=[])
        code = down.status
    if swapped(request, TABLE):
        return fragment(request, "deployments_table.html", {**context, "swapped": True}, code)
    flashes = [context["failure"]] if "failure" in context else []
    return render(request, "deployments.html", {**context, "flashes": flashes}, code)


async def choices(session: Session) -> dict[str, Any]:
    scenarios = await read(session, "/api/v1/scenarios")
    trainees = await read(session, "/api/v1/users", {"role": "trainee"})
    agents = await read(session, "/api/v1/agents")
    return {
        "scenarios": sorted(scenarios, key=lambda row: (row["slug"], -row["version"])),
        "trainees": [row for row in trainees if row["state"] == "enabled"],
        "agents": [row for row in agents["agents"] if row["state"] == "enabled"],
    }


def form_page(request: Request, context: dict[str, Any], status: int = 200) -> Response:
    return render(request, "deployment_new.html", {"current": "/deployments", **context}, status)


@router.get("/deployments/new")
async def new_deployment(request: Request, session: OperatorDep) -> Response:
    try:
        return form_page(request, {**await choices(session), "chosen": {}})
    except ApiDown as down:
        return form_page(request, {"flashes": [error(down.message)], "chosen": {}}, down.status)


@router.post("/deployments/new")
async def create_deployment(request: Request, session: PostedOperatorDep) -> Response:
    form = await request.form()
    chosen = {name: field(form, name) for name in ("scenario_id", "trainee_id", "agent_id")}
    payload = {name: value for name, value in chosen.items() if value}
    try:
        response = await session.call("POST", "/api/v1/deployments", payload)
        if response.status_code != 201:
            flash, errors = refused(response, CONCERNS)
            context = {**await choices(session), "chosen": chosen, "errors": errors}
            return form_page(request, {**context, "flashes": [flash]}, response.status_code)
    except ApiDown as down:
        try:
            context = await choices(session)
        except ApiDown:
            context = {}
        context.update(chosen=chosen, flashes=[error(down.message)])
        return form_page(request, context, down.status)
    return await created(request, session, response.json())


async def created(request: Request, session: Session, row: dict[str, Any]) -> Response:
    """The one answer that shows the trainee link; names that fail to load never lose it."""
    try:
        users = by_id(await read(session, "/api/v1/users"))
        scenarios = by_id(await read(session, "/api/v1/scenarios"))
    except (ApiDown, Refusal):
        users, scenarios = {}, {}
    label = described(row, scenarios, users)
    flash = Flash(
        "success", "Deployment created", f"{label} is {row['status']} until an agent picks it up."
    )
    context = {
        "current": "/deployments",
        "deployment": row,
        "label": label,
        "trainee": users.get(row["trainee_id"], {}).get("name", "the trainee"),
        "link": f"{str(request.base_url).rstrip('/')}/s/{row['link']['token']}",
        "flashes": [flash],
    }
    return render(request, "deployment_created.html", context)


async def labelled(session: Session, row: dict[str, Any]) -> str:
    scenarios = by_id(await read(session, "/api/v1/scenarios"))
    return described(row, scenarios, by_id(await read(session, "/api/v1/users")))


@router.get("/deployments/{deployment_id:uuid}/stop")
async def stop_dialog(request: Request, session: OperatorDep, deployment_id: uuid.UUID) -> Response:
    label = await labelled(session, await read(session, f"/api/v1/deployments/{deployment_id}"))
    context = {"id": deployment_id, "label": label, "next": came_from(request, "/deployments")}
    return fragment(request, "deployment_stop.html", context)


@router.post("/deployments/{deployment_id:uuid}/stop")
async def stop(request: Request, session: PostedOperatorDep, deployment_id: uuid.UUID) -> Response:
    back = local(field(await request.form(), "next")) or "/deployments"
    response = await session.call("POST", f"/api/v1/deployments/{deployment_id}/stop")
    if response.status_code != 200:
        return redirect(request, back, refused(response)[0])
    row = response.json()
    label = await labelled(session, row)
    if row["status"] == "STOPPING":
        text = f"{label} is STOPPING: the VM is destroyed and its links are revoked."
    else:
        text = f"{label} is {row['status']}."
    return redirect(request, back, Flash("success", "Stop requested", text))
