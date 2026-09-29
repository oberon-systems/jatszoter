from enum import StrEnum

from sqlmodel import col, select

from jatszoter_api.models._base import session_of
from jatszoter_api.models.link import Link
from jatszoter_api.models.secret import SecretOwner


class Role(StrEnum):
    OPERATOR = "operator"
    VIEWER = "viewer"
    TRAINEE = "trainee"


class User(SecretOwner, table=True):
    __tablename__ = "users"

    name: str
    role: Role
    comment: str | None = None

    @property
    def links(self) -> list[Link]:
        statement = select(Link).where(col(Link.user_id) == self.id)
        return list(session_of(self).exec(statement))
