import hashlib
import hmac
import secrets
import time
from enum import StrEnum
from typing import Any, Self

from pydantic import PrivateAttr
from sqlmodel import Field, Session

from jatszoter_api.errors import (
    ConflictError,
    InvalidPasswordError,
    NotAuthorizedError,
    ThrottledError,
)
from jatszoter_api.models._base import State
from jatszoter_api.models._cache import Cached
from jatszoter_api.models.link import Link
from jatszoter_api.models.secret import SecretOwner

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
MIN_PASSWORD_LENGTH = 12
LOGIN_FAILURES = 10
LOGIN_WINDOW_SECONDS = 15 * 60
ADMIN = "admin"
# Checked against for an unknown name, so it costs one scrypt like a real user.
_DECOY = f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${'00' * 16}${'00' * 32}"


class Role(StrEnum):
    OPERATOR = "operator"
    VIEWER = "viewer"
    TRAINEE = "trainee"


def _scrypt(plain: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    """The one scrypt call, so hashing and checking derive the same way."""
    return hashlib.scrypt(plain.encode(), salt=salt, n=n, r=r, p=p, dklen=32)


def _matches(plain: str, stored: str) -> bool:
    """Check `plain` against a stored `scrypt$n$r$p$salt$hash` in constant time."""
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        expected = bytes.fromhex(digest)
        actual = _scrypt(plain, bytes.fromhex(salt), int(n), int(r), int(p))
    except ValueError:
        return False
    return scheme == "scrypt" and hmac.compare_digest(actual, expected)


def _hashed(plain: str) -> str:
    """A salted scrypt hash with its parameters, never the password itself."""
    salt = secrets.token_bytes(16)
    digest = _scrypt(plain, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def _login_key(name: str) -> str:
    """The key of a name's failure counter, hashed so no name lands in the cache."""
    return f"login:{hashlib.sha256(name.encode()).hexdigest()}"


class User(SecretOwner, table=True):
    __tablename__ = "users"

    name: str = Field(unique=True)
    role: Role
    comment: str | None = None
    password: str | None = None
    # 0 until the user sets a password of their own, then when they did.
    password_changed: int = 0
    _links: list[Link] | None = PrivateAttr(default=None)

    def __init__(self, session: Session, **data: Any) -> None:
        super().__init__(session, **data)
        if self.password is not None:
            self.password = _hashed(self.password)

    @property
    def links(self) -> list[Link]:
        if not self._links:
            self._links = Link._load(self._session, user_id=self.id)
        return self._links

    def login(self, password: str) -> None:
        """Check `password` and the state of this user; failures per name are throttled."""
        key = _login_key(self.name)
        if self._count(key) >= LOGIN_FAILURES:
            raise ThrottledError("too many attempts")
        if not self.check_password(password) or self.state is not State.ENABLED:
            self._increment(key, LOGIN_WINDOW_SECONDS)
            raise NotAuthorizedError("not authorized")
        Cached._delete(self, key)

    def logout(self) -> None:
        """End every credential issued to this user so far."""
        secret = self.stored_secret
        secret.generation = max(secret.generation, secret.pending_generation or 0) + 1
        secret.pending_generation = None
        secret.save()
        self._delete("secret")

    def check_password(self, plain: str) -> bool:
        """Whether `plain` is the password; a user without one still costs a scrypt."""
        return _matches(plain, self.password or _DECOY) and self.password is not None

    def superseded(self) -> None:
        """Nothing: a user's key never leaves `web`, whose tabs race the refresh."""

    def switch(self, state: State) -> Self:
        """Enable or disable this user; the last enabled operator is never disabled."""
        if state is State.DISABLED and self.role is Role.OPERATOR and self.state is State.ENABLED:
            enabled = User._load(self._session, role=Role.OPERATOR, state=State.ENABLED)
            if len(enabled) <= 1:
                raise ConflictError("cannot disable the last enabled operator")
        self.state = state
        self.save()
        return self

    def reset_password(self, password: str) -> None:
        """Set a one-time password, which the next login must change."""
        if len(password) < MIN_PASSWORD_LENGTH:
            raise InvalidPasswordError(f"a password needs {MIN_PASSWORD_LENGTH} characters")
        self.password = _hashed(password)
        self.password_changed = 0
        self.save()

    def change_password(self, old: str, new: str) -> None:
        """Replace the password and mark it changed, refusing a short or unchanged one."""
        if len(new) < MIN_PASSWORD_LENGTH or new == old:
            raise InvalidPasswordError(
                f"a new password needs {MIN_PASSWORD_LENGTH} characters and must differ"
            )
        self.password = _hashed(new)
        self.password_changed = int(time.time())
        self.save()
