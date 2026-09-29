from enum import StrEnum

from pydantic import PrivateAttr

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
    _links: list[Link] | None = PrivateAttr(default=None)

    @property
    def links(self) -> list[Link]:
        if not self._links:
            self._links = Link._load(self._session, user_id=self.id)
        return self._links
