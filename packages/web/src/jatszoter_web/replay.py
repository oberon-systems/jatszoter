import json
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from jatszoter_web.api import ApiDown
from jatszoter_web.middleware import allow_wasm
from jatszoter_web.pages import error, refusal, render
from jatszoter_web.screens import Row, by_id, described, read, refused
from jatszoter_web.session import OperatorDep, Session

router = APIRouter()

CAST_TYPE = "application/x-asciicast"
NOT_FOUND = refusal("recording not found", 404)


def markers(cast: bytes) -> list[tuple[float, str]]:
    """The `m` events of an asciicast v2 file, in order; any other line is skipped."""
    found: list[tuple[float, str]] = []
    for line in cast.splitlines()[1:]:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(event, list)
            and len(event) == 3
            and event[1] == "m"
            and isinstance(event[0], int | float)
            and isinstance(event[2], str)
        ):
            found.append((float(event[0]), event[2]))
    return found


async def recorded(
    session: Session, deployment_id: uuid.UUID, recording_id: uuid.UUID
) -> Row | None:
    """The recording when it is one of the deployment's, else None."""
    rows = await read(session, f"/api/v1/deployments/{deployment_id}/recordings")
    return next((row for row in rows if row["id"] == str(recording_id)), None)


@router.get("/deployments/{deployment_id:uuid}/recordings/{recording_id:uuid}")
async def replay(
    request: Request, session: OperatorDep, deployment_id: uuid.UUID, recording_id: uuid.UUID
) -> Response:
    base = f"/api/v1/deployments/{deployment_id}"
    row = await read(session, base)
    users = by_id(await read(session, "/api/v1/users"))
    scenarios = by_id(await read(session, "/api/v1/scenarios"))
    recording = await recorded(session, deployment_id, recording_id)
    context: dict[str, Any] = {
        "current": "/deployments",
        "deployment": row,
        "label": described(row, scenarios, users),
        "recording": recording,
    }
    if recording is None:
        return render(request, "replay.html", {**context, "flashes": [NOT_FOUND]}, 404)
    link = by_id(await read(session, f"{base}/links")).get(recording["link_id"])
    owner = users.get(link["user_id"]) if link else None
    context.update(holder=owner["name"] if owner else "-", live=not (link and link["revoked_at"]))
    try:
        response = await session.call("GET", f"/api/v1/recordings/{recording_id}.cast")
    except ApiDown as down:
        page = {**context, "markers": [], "failure": True, "flashes": [error(down.message)]}
        return render(request, "replay.html", page, down.status)
    if response.status_code != 200:
        flash = NOT_FOUND if response.status_code == 404 else refused(response)[0]
        return render(request, "replay.html", {**context, "flashes": [flash]}, response.status_code)
    allow_wasm(request)
    context.update(player=True, markers=markers(response.content))
    return render(request, "replay.html", context)


@router.get("/deployments/{deployment_id:uuid}/recordings/{recording_id:uuid}.cast")
async def cast(session: OperatorDep, deployment_id: uuid.UUID, recording_id: uuid.UUID) -> Response:
    """The asciicast through a signed call; the file store's URL never reaches the browser."""
    headers = {"cache-control": "no-store"}
    if await recorded(session, deployment_id, recording_id) is None:
        return Response(status_code=404, headers=headers)
    response = await session.call("GET", f"/api/v1/recordings/{recording_id}.cast")
    if response.status_code != 200:
        return Response(status_code=response.status_code, headers=headers)
    return Response(response.content, media_type=CAST_TYPE, headers=headers)
