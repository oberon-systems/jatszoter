import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from jatszoter_api.auth import Party, require_deployment_party
from jatszoter_api.errors import ForbiddenError, NotFoundError
from jatszoter_api.models import Check, CheckStatus, Deployment, Role
from jatszoter_api.routes.deps import MarkDep, SessionDep

PartyDep = Annotated[Party, Depends(require_deployment_party)]


class CheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: CheckStatus
    exit_code: int | None
    message: str | None
    created_at: int
    finished_at: int | None


class CheckDetail(CheckOut):
    detail: str | None


def check_out(check: Check, party: Party) -> dict[str, Any]:
    """A check as its reader may see it: `detail` is there for an operator alone."""
    shape = CheckDetail if party.user.role is Role.OPERATOR else CheckOut
    return shape.model_validate(check).model_dump(mode="json")


def _deployment(session: SessionDep, deployment_id: uuid.UUID) -> Deployment:
    try:
        return Deployment(session=session).load(id=deployment_id)
    except IndexError:
        raise NotFoundError(f"deployment {deployment_id} is not known") from None


# No router dependency: each route takes the party, a signed operator or a link.
router = APIRouter(prefix="/deployments")


@router.post("/{deployment_id}/checks", status_code=201)
def request(
    session: SessionDep, party: PartyDep, mark: MarkDep, deployment_id: uuid.UUID
) -> dict[str, Any]:
    deployment = _deployment(session, deployment_id)
    if party.user.role is Role.VIEWER:
        raise ForbiddenError("a viewer requests no check")
    check = Check(
        session=session,
        deployment_id=deployment.id,
        link_id=None if party.link is None else party.link.id,
        user_id=party.user.id,
    )
    for settled in deployment.ask(check):
        mark(settled)
    return check_out(check, party)


@router.get("/{deployment_id}/checks")
def index(
    session: SessionDep, party: PartyDep, mark: MarkDep, deployment_id: uuid.UUID
) -> list[dict[str, Any]]:
    deployment = _deployment(session, deployment_id)
    for settled in deployment.expire_checks():
        mark(settled)
    stored = sorted(deployment.checks, key=lambda row: row.created_at)
    return [check_out(check, party) for check in stored]


@router.get("/{deployment_id}/checks/{check_id}")
def show(
    session: SessionDep,
    party: PartyDep,
    mark: MarkDep,
    deployment_id: uuid.UUID,
    check_id: uuid.UUID,
) -> dict[str, Any]:
    deployment = _deployment(session, deployment_id)
    for settled in deployment.expire_checks():
        mark(settled)
    try:
        check = Check(session=session).load(id=check_id, deployment_id=deployment.id)
    except IndexError:
        raise NotFoundError(f"check {check_id} is not known") from None
    return check_out(check, party)
