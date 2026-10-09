import hashlib
import hmac
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from jatszoter_api.auth import LINK_COOKIE, require_operator
from jatszoter_api.errors import NotFoundError
from jatszoter_api.models import Link, Role, State, User
from jatszoter_api.routes.deps import SessionDep


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


router = APIRouter(prefix="/links", dependencies=[Depends(require_operator)])
# Mounted outside /api/v1: the address a link's holder opens.
open_router = APIRouter()


@router.post("/{link_id}/revoke")
def revoke(request: Request, session: SessionDep, link_id: uuid.UUID) -> LinkOut:
    try:
        link = Link(session=session).load(id=link_id)
    except IndexError:
        raise NotFoundError(f"link {link_id} is not known") from None
    link.revoke()
    request.app.state.console_hub.revoke(link.id)
    return link_out(session, link)


@open_router.get("/s/{token}")
def open_link(session: SessionDep, token: str) -> RedirectResponse:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    try:
        link = Link(session=session).load(token_hash=token_hash)
        user = User(session=session).load(id=link.user_id)
    except IndexError:
        raise NotFoundError("not found") from None
    known = hmac.compare_digest(link.token_hash, token_hash)
    if not known or not link.live or user.state is not State.ENABLED:
        raise NotFoundError("not found")
    response = RedirectResponse(
        f"/t/{link.deployment_id}",
        status_code=303,
        headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"},
    )
    response.set_cookie(
        LINK_COOKIE,
        link.open(),
        path=f"/api/v1/deployments/{link.deployment_id}",
        httponly=True,
        secure=True,
        samesite="strict",
    )
    return response
