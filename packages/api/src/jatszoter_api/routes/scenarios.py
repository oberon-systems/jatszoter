import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from starlette.datastructures import UploadFile

from jatszoter_api import bundles
from jatszoter_api.auth import require_operator
from jatszoter_api.errors import InvalidBundleError, TooLargeError
from jatszoter_api.models import Image, Scenario
from jatszoter_api.routes.agents import StrictModel
from jatszoter_api.routes.deps import FetchUrl, SessionDep
from jatszoter_api.settings import get_settings

FETCH_TIMEOUT = 30.0


class UrlIn(StrictModel):
    url: FetchUrl


class ScenarioOut(BaseModel):
    id: uuid.UUID
    slug: str
    version: int
    title: str
    image: str


def scenario_out(scenario: Scenario) -> ScenarioOut:
    return ScenarioOut(
        id=scenario.id,
        slug=scenario.slug,
        version=scenario.version,
        title=scenario.document["title"],
        image=scenario.document["image"],
    )


def fetch_transport() -> httpx.BaseTransport | None:
    """The transport bundle downloads go through; tests override it."""
    return None


async def _submitted(request: Request) -> bytes | str:
    """The uploaded bundle, or the URL to fetch it from."""
    limit = get_settings().scenario_bundle_limit_bytes
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form(max_files=1, max_fields=0)
        bundle = form.get("bundle")
        if not isinstance(bundle, UploadFile):
            raise InvalidBundleError("the multipart form carries no bundle file")
        data = await bundle.read(limit + 1)
        if len(data) > limit:
            raise TooLargeError(f"the bundle is larger than {limit} bytes")
        return data
    try:
        return UrlIn.model_validate_json(await request.body()).url
    except ValidationError as error:
        errors = error.errors(include_url=False, include_context=False)
        raise RequestValidationError(errors) from None


def _fetch(url: str, transport: httpx.BaseTransport | None) -> bytes:
    """GET a bundle without following redirects, cut off past the size limit."""
    limit = get_settings().scenario_bundle_limit_bytes
    try:
        with (
            httpx.Client(
                transport=transport, timeout=FETCH_TIMEOUT, follow_redirects=False
            ) as client,
            client.stream("GET", url) as response,
        ):
            if response.is_redirect:
                raise InvalidBundleError("the bundle URL redirects; name the target instead")
            if response.status_code != 200:
                raise InvalidBundleError(f"the bundle URL answered {response.status_code}")
            data = bytearray()
            for chunk in response.iter_bytes():
                data += chunk
                if len(data) > limit:
                    raise TooLargeError(f"the bundle is larger than {limit} bytes")
            return bytes(data)
    except httpx.HTTPError as error:
        raise InvalidBundleError(
            f"the bundle URL could not be fetched: {type(error).__name__}"
        ) from None


router = APIRouter(prefix="/scenarios", dependencies=[Depends(require_operator)])


@router.post("", status_code=201)
def create(
    submitted: Annotated[bytes | str, Depends(_submitted)],
    transport: Annotated[httpx.BaseTransport | None, Depends(fetch_transport)],
    session: SessionDep,
) -> ScenarioOut:
    bundle = submitted if isinstance(submitted, bytes) else _fetch(submitted, transport)
    checked = bundles.read(bundle, get_settings().scenario_bundle_limit_bytes)
    image = checked.document["image"]
    try:
        Image(session=session).load(name=image)
    except IndexError:
        raise InvalidBundleError(f"image {image} is not registered") from None
    scenario = Scenario(
        session=session,
        slug=checked.document["id"],
        version=checked.document["version"],
        document=checked.document,
        digest=checked.digest,
    )
    scenario.save(bundle)
    return scenario_out(scenario)


@router.get("")
def index(session: SessionDep) -> list[ScenarioOut]:
    return [scenario_out(scenario) for scenario in Scenario._load(session)]
