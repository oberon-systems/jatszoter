import hashlib
import secrets
import uuid
from typing import Any, Self

from sqlmodel import Field, Session

from jatszoter_api.models._base import Base
from jatszoter_api.models.recording import Recording


class Link(Base, table=True):
    __tablename__ = "links"

    deployment_id: uuid.UUID = Field(index=True)
    user_id: uuid.UUID = Field(index=True)
    token_hash: str = Field(unique=True)
    opened_at: int | None = None
    revoked_at: int | None = None

    @property
    def recording(self) -> Recording | None:
        return next(iter(Recording._load(self._session, link_id=self.id)), None)

    def load(self, **key: Any) -> list[Self]:
        return self._load(self._session, **key)

    @classmethod
    def issue(
        cls, session: Session, deployment_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[Self, str]:
        """Store a link; the plain token exists only in this return value."""
        token = secrets.token_urlsafe(32)
        link = cls(
            session=session,
            deployment_id=deployment_id,
            user_id=user_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        link._save()
        return link, token
