import asyncio
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
from jatszoter_api.file import File
from jatszoter_api.models import Image, Scenario
from jatszoter_api.outbound import CheckedTransport, RefusedAddress
from jatszoter_api.routes.agents import StrictModel
from jatszoter_api.routes.deps import FetchUrl, SessionDep, SettingsDep
from jatszoter_api.settings import Settings, app_settings

FETCH_DEADLINE = 30.0
ADDRESS_REFUSED = "the bundle URL names an address the API may not fetch"


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


def fetch_transport(settings: SettingsDep) -> httpx.AsyncBaseTransport:
    """The transport bundle downloads go through; tests override it."""
    return CheckedTransport(settings.scenario_fetch_internal_cidrs)


def _failed(url: str, cause: str) -> InvalidBundleError:
    """The one answer to a failed fetch: a status and an exception must not map a network."""
    host = httpx.URL(url).host
    return InvalidBundleError(
        f"bundle fetch from {host} failed: {cause}", code="bundle-fetch-failed"
    )


async def _fetch(url: str, transport: httpx.AsyncBaseTransport, limit: int) -> bytes:
    """GET a bundle as sent, without redirects, cut off past `limit` bytes or the deadline."""
    try:
        # The whole GET sits under one deadline: a check between chunks misses a stall
        # in the headers or the chunk framing.
        async with (
            asyncio.timeout(FETCH_DEADLINE),
            httpx.AsyncClient(
                transport=transport,
                timeout=FETCH_DEADLINE,
                follow_redirects=False,
                # A proxy from the environment would take the request past the address check.
                trust_env=False,
                headers={"Accept-Encoding": "identity"},
            ) as client,
            client.stream("GET", url) as response,
        ):
            if response.is_redirect:
                raise InvalidBundleError(
                    "the bundle URL redirects; name the target instead", code="bundle-url-redirects"
                )
            if response.status_code != 200:
                raise _failed(url, f"status {response.status_code}")
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise InvalidBundleError(
                    "the bundle URL answered with a Content-Encoding", code="bundle-url-encoded"
                )
            data = bytearray()
            # Not aiter_bytes: it inflates a whole chunk before the limit sees it.
            async for chunk in response.aiter_raw():
                data += chunk
                if len(data) > limit:
                    raise TooLargeError(
                        f"the bundle is larger than {limit} bytes", code="bundle-too-large"
                    )
            return bytes(data)
    except TimeoutError:
        raise InvalidBundleError(
            f"the bundle URL did not finish within {FETCH_DEADLINE:.0f} seconds",
            code="bundle-fetch-timeout",
        ) from None
    except RefusedAddress:
        raise InvalidBundleError(ADDRESS_REFUSED, code="bundle-fetch-refused") from None
    except httpx.HTTPError as error:
        raise _failed(url, type(error).__name__) from None


async def _submitted(
    request: Request,
    settings: Annotated[Settings, Depends(app_settings)],
    transport: Annotated[httpx.AsyncBaseTransport, Depends(fetch_transport)],
) -> bytes:
    """The uploaded bundle, or the one fetched from the URL the request names."""
    limit = settings.scenario_bundle_limit_bytes
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form(max_files=1, max_fields=0)
        bundle = form.get("bundle")
        if not isinstance(bundle, UploadFile):
            raise InvalidBundleError(
                "the multipart form carries no bundle file", code="bundle-file-missing"
            )
        data = await bundle.read(limit + 1)
        if len(data) > limit:
            raise TooLargeError(f"the bundle is larger than {limit} bytes", code="bundle-too-large")
        return data
    try:
        url = UrlIn.model_validate_json(await request.body()).url
    except ValidationError as error:
        errors = error.errors(include_url=False, include_context=False)
        raise RequestValidationError(errors) from None
    return await _fetch(url, transport, limit)


router = APIRouter(prefix="/scenarios", dependencies=[Depends(require_operator)])


@router.post("", status_code=201)
def create(
    bundle: Annotated[bytes, Depends(_submitted)],
    session: SessionDep,
    settings: SettingsDep,
) -> ScenarioOut:
    checked = bundles.read(bundle, settings.scenario_bundle_limit_bytes)
    image = checked.document["image"]
    try:
        Image(session=session).load(name=image)
    except IndexError:
        raise InvalidBundleError(
            f"image {image} is not registered", code="image-not-registered"
        ) from None
    scenario = Scenario(
        session=session,
        slug=checked.document["id"],
        version=checked.document["version"],
        document=checked.document,
        digest=checked.digest,
    )
    filer_url = None if settings.seaweed_url is None else str(settings.seaweed_url)
    scenario.save(File(scenario.bundle_path, bundle, filer_url))
    return scenario_out(scenario)


@router.get("")
def index(session: SessionDep) -> list[ScenarioOut]:
    return [scenario_out(scenario) for scenario in Scenario._load(session)]
