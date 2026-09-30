import hashlib
import hmac
import secrets
import time
from enum import StrEnum
from typing import Self

from pydantic import PrivateAttr
from sqlmodel import Session

from jatszoter_api.errors import InvalidPasswordError, NotAuthorizedError, ThrottledError
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


def _login_key(name: str) -> str:
    """The key of a name's failure counter, hashed so no name lands in the cache."""
    return f"login:{hashlib.sha256(name.encode()).hexdigest()}"


class User(SecretOwner, table=True):
    __tablename__ = "users"

    name: str
    role: Role
    comment: str | None = None
    password: str | None = None
    # 0 until the user sets a password of their own, then when they did.
    password_changed: int = 0
    _links: list[Link] | None = PrivateAttr(default=None)

    @property
    def links(self) -> list[Link]:
        if not self._links:
            self._links = Link._load(self._session, user_id=self.id)
        return self._links

    @classmethod
    def by_name(cls, session: Session, name: str) -> Self | None:
        """The user a login names, or None."""
        return next(iter(cls._load(session, name=name)), None)

    @classmethod
    def ensure_admin(cls, session: Session) -> Self | None:
        """Create `admin` / `admin`, due for a password change, when no operator exists."""
        if cls._load(session, role=Role.OPERATOR):
            return None
        admin = cls(session=session, name=ADMIN, role=Role.OPERATOR, state=State.ENABLED)
        admin.set_password(ADMIN)
        admin.save()
        return admin

    @classmethod
    def log_in(cls, session: Session, name: str, password: str) -> Self:
        """The enabled user `name` if `password` matches; failures per name are throttled."""
        key = _login_key(name)
        if Cached()._count(key) >= LOGIN_FAILURES:
            raise ThrottledError("too many attempts")
        user = cls.by_name(session, name)
        if user is None:
            _matches(password, _DECOY)
        if user is None or not user.check_password(password) or user.state is not State.ENABLED:
            Cached()._increment(key, LOGIN_WINDOW_SECONDS)
            raise NotAuthorizedError("not authorized")
        Cached()._delete(key)
        return user

    def set_password(self, plain: str) -> None:
        """Keep a salted scrypt hash with its parameters, never the password itself."""
        salt = secrets.token_bytes(16)
        digest = _scrypt(plain, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
        self.password = f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"

    def check_password(self, plain: str) -> bool:
        """Whether `plain` is the password; a user without one still costs a scrypt."""
        return _matches(plain, self.password or _DECOY) and self.password is not None

    def change_password(self, old: str, new: str) -> None:
        """Replace the password and mark it changed, refusing a short or unchanged one."""
        if len(new) < MIN_PASSWORD_LENGTH or new == old:
            raise InvalidPasswordError(
                f"a new password needs {MIN_PASSWORD_LENGTH} characters and must differ"
            )
        self.set_password(new)
        self.password_changed = int(time.time())
        self.save()
