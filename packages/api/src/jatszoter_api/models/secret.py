import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from typing import Any, Self

from sqlmodel import Field, Session

from jatszoter_api.models._base import Base, State
from jatszoter_api.settings import get_settings

SIGNATURE_WINDOW_SECONDS = 300
CREDENTIAL_SECONDS = 60 * 60
REFRESH_BEFORE_SECONDS = 15 * 60


@dataclass(frozen=True)
class Credential:
    """What an owner presents with every request; none of it is secret."""

    owner_id: uuid.UUID
    kid: str
    generation: int
    expires_at: int

    @property
    def expired(self) -> bool:
        return int(time.time()) >= self.expires_at

    @property
    def rotation_due(self) -> bool:
        return self.expires_at - int(time.time()) <= REFRESH_BEFORE_SECONDS

    @property
    def key(self) -> str | None:
        """HMAC of the credential under the master key it names; the API stores no key."""
        master = get_settings().master_keys.get(self.kid)
        if master is None:
            return None
        message = f"{self.owner_id}:{self.generation}:{self.expires_at}".encode()
        return hmac.new(master.encode(), message, hashlib.sha256).hexdigest()

    def sign(self, method: str, path: str, body: bytes, timestamp: int) -> str | None:
        """HMAC-SHA256 of `ts\\nMETHOD\\npath\\nsha256(body)`; the owner signs the same way."""
        key = self.key
        if key is None:
            return None
        message = "\n".join(
            (str(timestamp), method.upper(), path, hashlib.sha256(body).hexdigest())
        )
        return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


class Secret(Base, table=True):
    __tablename__ = "secrets"

    owner_id: uuid.UUID = Field(unique=True)
    generation: int = 1
    pending_generation: int | None = None


@dataclass(frozen=True)
class Generations:
    """What `verify` reads of a `Secret`, kept in the cache."""

    generation: int
    pending_generation: int | None


class SecretOwner(Base):
    state: State = State.DISABLED

    @classmethod
    def load(cls, session: Session, **key: Any) -> Self:
        return cls._load(session, **key)[0]

    def save(self) -> None:
        self._save()

    @property
    def secret(self) -> Generations | None:
        """The owner's generations for `verify`, loaded into the cache on a miss."""
        generations: Generations | None = self._get("secret", self._stored_generations)
        return generations

    def _stored_generations(self) -> Generations | None:
        stored = self._stored_secret()
        if stored is None:
            return None
        return Generations(stored.generation, stored.pending_generation)

    def issue(self, ttl: int) -> tuple[Credential, str]:
        """First call opens generation 1; later calls hand out a fresh pending generation."""
        record = self._stored_secret()
        if record is None:
            generation = 1
            Secret(session=self._session, owner_id=self.id, generation=generation)._save()
        else:
            generation = max(record.generation, record.pending_generation or 0) + 1
            record.pending_generation = generation
            record._save()
        self._delete("secret")
        credential = Credential(
            owner_id=self.id,
            kid=get_settings().master_kid,
            generation=generation,
            expires_at=int(time.time()) + ttl,
        )
        key = credential.key
        if key is None:
            raise RuntimeError(f"no master key for kid {credential.kid!r}")
        return credential, key

    def verify(
        self,
        credential: Credential,
        method: str,
        path: str,
        body: bytes,
        timestamp: int,
        signature: str,
        now: int | None = None,
    ) -> bool:
        if self.state is not State.ENABLED or credential.owner_id != self.id:
            return False
        if abs((int(time.time()) if now is None else now) - timestamp) > SIGNATURE_WINDOW_SECONDS:
            return False
        expected = credential.sign(method, path, body, timestamp)
        if credential.expired or expected is None:
            return False
        if not hmac.compare_digest(signature, expected):
            return False
        record = self.secret
        if record is None:
            return False
        if credential.generation == record.generation:
            return True
        if credential.generation == record.pending_generation:
            stored = self._stored_secret()
            if stored is not None:
                stored.generation = credential.generation
                stored.pending_generation = None
                stored._save()
            self._delete("secret")
            return True
        if credential.generation < record.generation:
            # A superseded generation in use means two parties hold it; only an operator
            # re-enables the owner.
            self.state = State.DISABLED
            self._save()
        return False

    def _stored_secret(self) -> Secret | None:
        return next(iter(Secret._load(self._session, owner_id=self.id)), None)
