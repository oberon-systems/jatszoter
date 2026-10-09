import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from jatszoter_api.auth import require_operator
from jatszoter_api.errors import NotFoundError
from jatszoter_api.models import Link, Role, User
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


@router.post("/{link_id}/revoke")
def revoke(session: SessionDep, link_id: uuid.UUID) -> LinkOut:
    try:
        link = Link(session=session).load(id=link_id)
    except IndexError:
        raise NotFoundError(f"link {link_id} is not known") from None
    link.revoke()
    return link_out(session, link)
