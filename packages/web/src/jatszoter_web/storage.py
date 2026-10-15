import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from jatszoter_web.api import ApiDown
from jatszoter_web.pages import Flash, counted, error, fragment, redirect, render
from jatszoter_web.screens import TERMINAL, by_id, described, read, refused
from jatszoter_web.session import OperatorDep, PostedOperatorDep, Session

router = APIRouter()


@router.get("/storage")
async def storage(request: Request, session: OperatorDep) -> Response:
    context: dict[str, Any] = {"current": "/storage", "terminal": TERMINAL}
    try:
        held = await read(session, "/api/v1/storage")
        context.update(
            held=held,
            deployments=by_id(await read(session, "/api/v1/deployments")),
            scenarios=by_id(await read(session, "/api/v1/scenarios")),
            users=by_id(await read(session, "/api/v1/users")),
        )
        code = 200
    except ApiDown as down:
        context.update(held=None, failure=True, flashes=[error(down.message)])
        code = down.status
    return render(request, "storage.html", context, code)


async def purgeable(session: Session, deployment_id: uuid.UUID) -> tuple[str, int]:
    """The deployment's name for a dialog or a flash, and how many recordings it holds."""
    row = await read(session, f"/api/v1/deployments/{deployment_id}")
    scenarios = by_id(await read(session, "/api/v1/scenarios"))
    label = described(row, scenarios, by_id(await read(session, "/api/v1/users")))
    held = await read(session, "/api/v1/storage")
    for item in held["deployments"]:
        if item["deployment_id"] == str(deployment_id):
            return label, int(item["recordings"])
    return label, 0


@router.get("/storage/{deployment_id:uuid}/purge")
async def purge_dialog(
    request: Request, session: OperatorDep, deployment_id: uuid.UUID
) -> Response:
    label, recordings = await purgeable(session, deployment_id)
    context = {"id": deployment_id, "label": label, "recordings": counted(recordings, "recording")}
    return fragment(request, "storage_purge.html", context)


@router.post("/storage/{deployment_id:uuid}/purge")
async def purge(request: Request, session: PostedOperatorDep, deployment_id: uuid.UUID) -> Response:
    response = await session.call("POST", f"/api/v1/deployments/{deployment_id}/purge")
    if response.status_code != 200:
        return redirect(request, "/storage", refused(response)[0])
    label, _ = await purgeable(session, deployment_id)
    text = f"The console log and recordings of {label} are deleted."
    return redirect(request, "/storage", Flash("success", "Purged", text))
