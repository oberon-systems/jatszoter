import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from jatszoter_web.api import ApiDown
from jatszoter_web.pages import error, redirect, render
from jatszoter_web.screens import TERMINAL, by_id, read, refused
from jatszoter_web.session import OperatorDep

router = APIRouter()


def held(row: dict[str, Any], console_bytes: int) -> str:
    """What a deployment's log row offers: the log, nothing yet, or nothing left."""
    if console_bytes > 0:
        return "log"
    if row["status"] in TERMINAL and row.get("vm_id"):
        return "purged"
    return "none"


@router.get("/logs")
async def logs(request: Request, session: OperatorDep) -> Response:
    context: dict[str, Any] = {"current": "/logs"}
    try:
        rows = await read(session, "/api/v1/deployments")
        storage = await read(session, "/api/v1/storage")
        context.update(
            rows=rows,
            scenarios=by_id(await read(session, "/api/v1/scenarios")),
            users=by_id(await read(session, "/api/v1/users")),
        )
        sizes = {item["deployment_id"]: item["console_bytes"] for item in storage["deployments"]}
        context["held"] = {row["id"]: held(row, sizes.get(row["id"], 0)) for row in rows}
        code = 200
    except ApiDown as down:
        context.update(rows=[], failure=True, flashes=[error(down.message)])
        code = down.status
    return render(request, "logs.html", context, code)


@router.get("/deployments/{deployment_id:uuid}/console.log")
async def console_log(request: Request, session: OperatorDep, deployment_id: uuid.UUID) -> Response:
    response = await session.call("GET", f"/api/v1/deployments/{deployment_id}/console")
    if response.status_code != 200:
        return redirect(request, "/logs", refused(response)[0])
    headers = {
        "cache-control": "no-store",
        "content-disposition": f'attachment; filename="console-{deployment_id}.log"',
    }
    return Response(response.content, media_type="application/octet-stream", headers=headers)
