import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass

from sqlmodel import Field, col, select

from jatszoter_api.models._base import Base, State, session_of
from jatszoter_api.models._cache import cached
from jatszoter_api.settings import get_settings

SIGNATURE_WINDOW_SECONDS = 300
REFRESH_BEFORE_SECONDS = 24 * 60 * 60


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


class SecretOwner(Base):
    state: State = State.DISABLED

    @cached(key=lambda owner: f"secret:{owner.id}")
    def secret(self) -> Secret | None:
        """The owner's generations, cached for `verify`; `del owner.secret` drops the copy."""
        return self._stored_secret()

    def issue(self, ttl: int) -> tuple[Credential, str]:
        """First call opens generation 1; later calls hand out a fresh pending generation."""
        record = self._stored_secret()
        if record is None:
            generation = 1
            Secret(owner_id=self.id, generation=generation)
            session_of(self).commit()
        else:
            generation = max(record.generation, record.pending_generation or 0) + 1
            record.set(pending_generation=generation)
        del self.secret
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
                stored.set(generation=credential.generation, pending_generation=None)
            del self.secret
            return True
        if credential.generation < record.generation:
            # A superseded generation in use means two parties hold it; only an operator
            # re-enables the owner.
            self.set(state=State.DISABLED)
        return False

    def _stored_secret(self) -> Secret | None:
        statement = select(Secret).where(col(Secret.owner_id) == self.id)
        return session_of(self).exec(statement).first()
