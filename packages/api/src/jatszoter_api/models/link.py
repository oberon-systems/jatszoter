import hashlib
import secrets
import uuid
from typing import Self

from sqlmodel import Field, col, select

from jatszoter_api.models._base import Base, session_of
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
        statement = select(Recording).where(col(Recording.link_id) == self.id)
        return session_of(self).exec(statement).first()

    @classmethod
    def issue(cls, deployment_id: uuid.UUID, user_id: uuid.UUID) -> tuple[Self, str]:
        """Build a link; the plain token exists only in this return value."""
        token = secrets.token_urlsafe(32)
        link = cls(
            deployment_id=deployment_id,
            user_id=user_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        return link, token
