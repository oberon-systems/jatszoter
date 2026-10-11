import uuid

import httpx
from fastapi import APIRouter, Depends, Response

from jatszoter_api.auth import require_operator
from jatszoter_api.errors import NotFoundError
from jatszoter_api.file import File
from jatszoter_api.models import Recording
from jatszoter_api.routes.deps import SessionDep, SettingsDep

router = APIRouter(prefix="/recordings", dependencies=[Depends(require_operator)])


@router.get("/{recording_id}.cast")
def download(session: SessionDep, settings: SettingsDep, recording_id: str) -> Response:
    """The asciicast as far as it is written; 404 for an unknown id or a file that is gone."""
    try:
        recording = Recording(session=session).load(id=uuid.UUID(recording_id))
    except (ValueError, IndexError):
        raise NotFoundError(f"recording {recording_id} is not known") from None
    filer_url = None if settings.seaweed_url is None else str(settings.seaweed_url)
    stored = File(recording.path, filer_url=filer_url)
    try:
        stored.get()
    except httpx.HTTPStatusError as error:
        if error.response.status_code != 404:
            raise
        raise NotFoundError(f"recording {recording.id} has no file") from None
    return Response(
        stored.data, media_type="application/x-asciicast", headers={"Cache-Control": "no-store"}
    )
