from typing import Any, Self

from sqlmodel import JSON, Column, Field, Session, UniqueConstraint

from jatszoter_api.file import File
from jatszoter_api.models._base import Base


class Scenario(Base, table=True):
    __tablename__ = "scenarios"
    __table_args__ = (UniqueConstraint("slug", "version"),)

    slug: str
    version: int
    document: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    bundle_url: str
    digest: str

    def __init__(self, session: Session, **data: Any) -> None:
        super().__init__(session, **data)

    def load(self, **key: Any) -> Self:
        return self._load(self._session, **key)[0]

    def save(self, bundle: bytes) -> None:
        file = File(f"scenarios/{self.slug}/{self.version}.tar.gz", bundle)
        self.bundle_url = file.url
        self._save()
        file.put()
