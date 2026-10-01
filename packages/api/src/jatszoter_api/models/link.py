import hashlib
import secrets
import uuid
from typing import Any, Self

from pydantic import PrivateAttr
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
    _token: str | None = PrivateAttr(default=None)
    _recording: Recording | None = PrivateAttr(default=None)

    def __init__(self, session: Session, **data: Any) -> None:
        super().__init__(session, **data)
        self._token = secrets.token_urlsafe(32)
        self.token_hash = hashlib.sha256(self._token.encode()).hexdigest()

    @property
    def token(self) -> str | None:
        """The plain token of a link made here; a loaded link never has it."""
        return self._token

    @property
    def recording(self) -> Recording:
        if not self._recording:
            self._recording = Recording._load(self._session, link_id=self.id)[0]
        return self._recording

    def load(self, **key: Any) -> Self:
        return self._load(self._session, **key)[0]

    def save(self) -> None:
        self._save()
