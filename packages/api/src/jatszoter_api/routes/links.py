import hashlib
import hmac
import logging
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from jatszoter_api.auth import LINK_COOKIE, require_operator
from jatszoter_api.errors import ConflictError, NotFoundError
from jatszoter_api.models import Deployment, Link, Recording, Role, State, User
from jatszoter_api.routes.deps import SessionDep, SettingsDep

log = logging.getLogger(__name__)


class LinkOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    role: Role
    created_at: int
    opened_at: int | None
    revoked_at: int | None


def link_out(session: SessionDep, link: Link) -> LinkOut:
    """A link with its user's role, and never its token or the hash."""
    user = User(session=session).load(id=link.user_id)
    return LinkOut(
        id=link.id,
        user_id=link.user_id,
        role=user.role,
        created_at=link.created_at,
        opened_at=link.opened_at,
        revoked_at=link.revoked_at,
    )


def _record(session: SessionDep, link: Link, filer_url: str | None, limit_bytes: int) -> None:
    """Start the recording of a trainee link; a failure is logged and the link still opens."""
    try:
        deployment = Deployment(session=session).load(id=link.deployment_id)
        scenario = deployment.scenario
        title = f"{scenario.slug} v{scenario.version} {deployment.id}"
        recording = Recording(session=session, deployment_id=deployment.id, link_id=link.id)
        recording.start(deployment.console, title, filer_url, limit_bytes)
    except ConflictError:
        # Another tab opened the link first and holds the recording.
        return
    except Exception as error:
        log.error(
            "recording of deployment %s not started: %s", link.deployment_id, type(error).__name__
        )


router = APIRouter(prefix="/links", dependencies=[Depends(require_operator)])
# Mounted outside /api/v1: the address a link's holder opens.
open_router = APIRouter()


@router.post("/{link_id}/revoke")
def revoke(
    request: Request, session: SessionDep, settings: SettingsDep, link_id: uuid.UUID
) -> LinkOut:
    try:
        link = Link(session=session).load(id=link_id)
    except IndexError:
        raise NotFoundError(f"link {link_id} is not known") from None
    link.revoke()
    request.app.state.console_hub.revoke(link.id)
    try:
        link.recording.close(None if settings.seaweed_url is None else str(settings.seaweed_url))
    except IndexError:
        pass
    except Exception as error:
        log.error(
            "recording of deployment %s not closed: %s", link.deployment_id, type(error).__name__
        )
    return link_out(session, link)


@open_router.get("/s/{token}")
def open_link(session: SessionDep, settings: SettingsDep, token: str) -> RedirectResponse:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    try:
        link = Link(session=session).load(token_hash=token_hash)
        user = User(session=session).load(id=link.user_id)
    except IndexError:
        raise NotFoundError("not found") from None
    known = hmac.compare_digest(link.token_hash, token_hash)
    if not known or not link.live or user.state is not State.ENABLED:
        raise NotFoundError("not found")
    first = link.opened_at is None
    session_token = link.open()
    if first and user.role is Role.TRAINEE:
        filer_url = None if settings.seaweed_url is None else str(settings.seaweed_url)
        _record(session, link, filer_url, settings.recording_limit_bytes)
    response = RedirectResponse(
        f"/t/{link.deployment_id}",
        status_code=303,
        headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"},
    )
    response.set_cookie(
        LINK_COOKIE,
        session_token,
        path=f"/api/v1/deployments/{link.deployment_id}",
        httponly=True,
        secure=True,
        samesite="strict",
    )
    return response
