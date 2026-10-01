import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from jatszoter_api.auth import require_operator
from jatszoter_api.models import Image
from jatszoter_api.routes.agents import StrictModel
from jatszoter_api.routes.deps import SessionDep, WebUrl


class ImageIn(StrictModel):
    name: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,63}$")]
    url: WebUrl
    digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    url: str
    digest: str


router = APIRouter(prefix="/images", dependencies=[Depends(require_operator)])


@router.post("", status_code=201)
def create(body: ImageIn, session: SessionDep) -> ImageOut:
    image = Image(session=session, name=body.name, url=body.url, digest=body.digest)
    image.save()
    return ImageOut.model_validate(image)


@router.get("")
def index(session: SessionDep) -> list[ImageOut]:
    return [ImageOut.model_validate(image) for image in Image._load(session)]
