import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from jatszoter_api.auth import require_operator
from jatszoter_api.models import Role, State, User
from jatszoter_api.routes.agents import StrictModel
from jatszoter_api.routes.deps import SessionDep


class UserIn(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    role: Role
    comment: Annotated[str, Field(max_length=1024)] | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    role: Role
    comment: str | None
    state: State
    created_at: int


router = APIRouter(prefix="/users", dependencies=[Depends(require_operator)])


@router.post("", status_code=201)
def create(body: UserIn, session: SessionDep) -> UserOut:
    user = User(
        session=session,
        name=body.name,
        role=body.role,
        comment=body.comment,
        state=State.ENABLED,
    )
    user.save()
    return UserOut.model_validate(user)


@router.get("")
def index(session: SessionDep, role: Role | None = None) -> list[UserOut]:
    key = {} if role is None else {"role": role}
    users = sorted(User._load(session, **key), key=lambda row: row.created_at, reverse=True)
    return [UserOut.model_validate(user) for user in users]
