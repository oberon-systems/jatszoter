from typing import Any, Self

from sqlmodel import Field, Session

from jatszoter_api.models._base import Base


class Image(Base, table=True):
    __tablename__ = "images"

    name: str = Field(unique=True)
    url: str
    digest: str

    def __init__(self, session: Session, **data: Any) -> None:
        super().__init__(session, **data)

    def load(self, **key: Any) -> Self:
        return self._load(self._session, **key)[0]

    def save(self) -> None:
        self._save()
