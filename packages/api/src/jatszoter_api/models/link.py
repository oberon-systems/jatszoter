import hashlib
import secrets
import time
import uuid
from typing import Any, Self

from pydantic import PrivateAttr
from sqlmodel import Field, Session

from jatszoter_api.models._base import Base
from jatszoter_api.models._cache import Cached
from jatszoter_api.models.recording import Recording

SESSION_SECONDS = 24 * 60 * 60


def _session_key(token: str) -> str:
    """The key of a session, hashed so no session token lands in the cache."""
    return f"session:{hashlib.sha256(token.encode()).hexdigest()}"


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

    @property
    def live(self) -> bool:
        return self.revoked_at is None

    def load(self, **key: Any) -> Self:
        return self._load(self._session, **key)[0]

    def save(self) -> None:
        self._save()

    def revoke(self) -> None:
        """Stamp `revoked_at` once; a repeated call changes nothing."""
        if self.revoked_at is None:
            self.revoked_at = int(time.time())
            self.save()

    def open(self) -> str:
        """Stamp `opened_at` on the first open and start a session; answer its token."""
        if self.opened_at is None:
            self.opened_at = int(time.time())
            self.save()
        token = secrets.token_urlsafe(32)
        Cached._set(self, _session_key(token), self.id, SESSION_SECONDS)
        return token

    def resume(self, token: str) -> Self:
        """The link a session token names, its session renewed; a miss is IndexError."""
        key = _session_key(token)
        link_id = Cached._get(self, key)
        if link_id is None:
            raise IndexError("no session")
        Cached._ttl(self, key, SESSION_SECONDS)
        return self.load(id=link_id)
