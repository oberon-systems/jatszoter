import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from jatszoter_api.auth import require_user
from jatszoter_api.errors import ForbiddenError
from jatszoter_api.models import User
from jatszoter_api.models.secret import CREDENTIAL_SECONDS
from jatszoter_api.routes.agents import CredentialOut, StrictModel, credential_out
from jatszoter_api.routes.deps import SessionDep

UserDep = Annotated[User, Depends(require_user)]

Password = Annotated[str, Field(min_length=1, max_length=1024)]


class LoginIn(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    password: Password
    new_password: Password | None = None


class LoginOut(BaseModel):
    user_id: uuid.UUID
    credential: CredentialOut


router = APIRouter(prefix="/auth")


@router.post("/login")
def login(body: LoginIn, session: SessionDep) -> LoginOut:
    """Trade a name and password for a credential, changing the password when asked or due."""
    try:
        user = User(session=session).load(name=body.name)
    except IndexError:
        # A stand-in without a password: it fails like a wrong password and costs one scrypt.
        user = User(session=session, name=body.name)
    user.login(body.password)
    if body.new_password is not None:
        user.change_password(body.password, body.new_password)
    elif not user.password_changed:
        raise ForbiddenError("password change required")
    return LoginOut(user_id=user.id, credential=credential_out(*user.issue(CREDENTIAL_SECONDS)))


@router.post("/credential")
def refresh(user: UserDep) -> CredentialOut:
    """A fresh credential for a signed user before theirs expires."""
    return credential_out(*user.issue(CREDENTIAL_SECONDS))


@router.post("/logout", status_code=204)
def logout(user: UserDep) -> None:
    """End every credential the signed user holds."""
    user.logout()
