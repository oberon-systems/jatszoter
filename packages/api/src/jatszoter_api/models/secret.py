import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from typing import Any, Self, cast

from pydantic import PrivateAttr
from sqlmodel import Field, Session

from jatszoter_api.models._base import Base, State

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

    def key(self, keys: dict[str, str]) -> str | None:
        """HMAC of the credential under the master key it names; the API stores no key."""
        master = keys.get(self.kid)
        if master is None:
            return None
        message = f"{self.owner_id}:{self.generation}:{self.expires_at}".encode()
        return hmac.new(master.encode(), message, hashlib.sha256).hexdigest()

    def sign(
        self, keys: dict[str, str], method: str, path: str, body: bytes, timestamp: int
    ) -> str | None:
        """HMAC-SHA256 of `ts\\nMETHOD\\npath\\nsha256(body)`; the owner signs the same way."""
        key = self.key(keys)
        if key is None:
            return None
        message = "\n".join(
            (str(timestamp), method.upper(), path, hashlib.sha256(body).hexdigest())
        )
        return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


class Secret(Base, table=True):
    __tablename__ = "secrets"

    owner_id: uuid.UUID = Field(unique=True)
    # 0 until the first credential is issued.
    generation: int = 0
    pending_generation: int | None = None

    def load(self, **key: Any) -> Self:
        return self._load(self._session, **key)[0]

    def save(self) -> None:
        self._save()


@dataclass(frozen=True)
class Generations:
    """What `verify` reads of a `Secret`, kept in the cache."""

    generation: int
    pending_generation: int | None


class SecretOwner(Base):
    state: State = State.DISABLED
    _secret: Secret | None = PrivateAttr(default=None)

    def __init__(self, session: Session, **data: Any) -> None:
        super().__init__(session, **data)
        self._secret = Secret(session=session, owner_id=self.id)

    def load(self, **key: Any) -> Self:
        return self._load(self._session, **key)[0]

    def save(self) -> None:
        self._save()
        self.stored_secret.save()

    @property
    def stored_secret(self) -> Secret:
        if not self._secret:
            self._secret = Secret._load(self._session, owner_id=self.id)[0]
        return self._secret

    @property
    def secret(self) -> Generations:
        """The owner's generations for `verify`, loaded into the cache on a miss."""
        return cast(Generations, self._get("secret", self._stored_generations))

    def _stored_generations(self) -> Generations:
        return Generations(self.stored_secret.generation, self.stored_secret.pending_generation)

    def issue(self, ttl: int, kid: str, keys: dict[str, str]) -> tuple[Credential, str]:
        """First call opens generation 1; later calls hand out a fresh pending generation."""
        secret = self.stored_secret
        if secret.generation == 0:
            generation = secret.generation = 1
        else:
            generation = max(secret.generation, secret.pending_generation or 0) + 1
            secret.pending_generation = generation
        secret.save()
        self._delete("secret")
        credential = Credential(
            owner_id=self.id,
            kid=kid,
            generation=generation,
            expires_at=int(time.time()) + ttl,
        )
        key = credential.key(keys)
        if key is None:
            raise RuntimeError(f"no master key for kid {credential.kid!r}")
        return credential, key

    def verify(
        self,
        credential: Credential,
        keys: dict[str, str],
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
        expected = credential.sign(keys, method, path, body, timestamp)
        if credential.expired or expected is None:
            return False
        if not hmac.compare_digest(signature, expected):
            return False
        record = self.secret
        if credential.generation == record.generation:
            return True
        if credential.generation == record.pending_generation:
            self.stored_secret.generation = credential.generation
            self.stored_secret.pending_generation = None
            self.stored_secret.save()
            self._delete("secret")
            return True
        if credential.generation < record.generation:
            # A superseded generation in use means two parties hold it; only an operator
            # re-enables the owner.
            self.state = State.DISABLED
            self.save()
        return False
