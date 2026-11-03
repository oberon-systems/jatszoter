import hashlib
import hmac
import ipaddress
import secrets
import time
from enum import StrEnum
from typing import Any, ClassVar, Self

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
WEAK = (
    f"at least {MIN_PASSWORD_LENGTH} characters and three of: "
    "lowercase, uppercase, digits, symbols"
)
UNCHANGED = "must differ from the current password"
LOGIN_FAILURES = 10
NAME_FAILURES = 100
LOGIN_WINDOW_SECONDS = 15 * 60
DEVICE_SECONDS = 90 * 24 * 60 * 60
ADMIN = "admin"
# Checked against for an unknown name, so it costs one scrypt like a real user.
_DECOY = f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${'00' * 16}${'00' * 32}"


class Role(StrEnum):
    OPERATOR = "operator"
    VIEWER = "viewer"
    TRAINEE = "trainee"


def weak(password: str) -> bool:
    """Whether `password` is short or mixes fewer than three of the four character classes."""
    classes = (str.islower, str.isupper, str.isdigit, lambda char: not char.isalnum())
    mixed = sum(any(map(test, password)) for test in classes)
    return len(password) < MIN_PASSWORD_LENGTH or mixed < 3


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


def _hashed_key(*parts: str) -> str:
    """A failure counter's key, hashed so no name, address or token lands in the cache."""
    return ":".join(["login", *(hashlib.sha256(part.encode()).hexdigest() for part in parts)])


def _client(address: str) -> str:
    """The address a client is counted by: an IPv6 one by its /64, a mapped IPv4 one as IPv4."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network((ip, 64), strict=False))
    return str(ip)


def _device_mac(master: str, user_id: str, expires_at: str, nonce: str) -> str:
    message = f"device:{user_id}:{expires_at}:{nonce}".encode()
    return hmac.new(master.encode(), message, hashlib.sha256).hexdigest()


class User(SecretOwner, table=True):
    __tablename__ = "users"
    CONFLICT: ClassVar[str] = "user-name-taken"

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

    def login(self, password: str, client: str, device: str | None, keys: dict[str, str]) -> None:
        """Check `password` and the state of this user. Failures are throttled per device for
        a valid device token, otherwise per client address and, higher, per name."""
        nonce = self._device(device, keys) if device else None
        if nonce is not None:
            counters = [(_hashed_key(self.name, "device", nonce), LOGIN_FAILURES)]
        else:
            counters = [
                (_hashed_key(self.name, "client", _client(client)), LOGIN_FAILURES),
                (_hashed_key(self.name), NAME_FAILURES),
            ]
        if any(self._count(key) >= limit for key, limit in counters):
            raise ThrottledError("too many attempts", code="login-throttled")
        if not self.check_password(password) or self.state is not State.ENABLED:
            for key, _ in counters:
                self._increment(key, LOGIN_WINDOW_SECONDS)
            raise NotAuthorizedError("not authorized")
        for key, _ in counters:
            Cached._delete(self, key)

    def device_token(self, kid: str, keys: dict[str, str]) -> str:
        """`kid.expires_at.nonce.mac`: what marks a browser this user logged in from."""
        expires_at = str(int(time.time()) + DEVICE_SECONDS)
        nonce = secrets.token_urlsafe(16)
        mac = _device_mac(keys[kid], str(self.id), expires_at, nonce)
        return ".".join((kid, expires_at, nonce, mac))

    def _device(self, token: str, keys: dict[str, str]) -> str | None:
        """The nonce of a live device token issued to this user; None for any other token."""
        parts = token.rsplit(".", 3)
        if len(parts) != 4 or parts[0] not in keys:
            return None
        if not (parts[1].isascii() and parts[1].isdigit()):
            return None
        kid, expires_at, nonce, mac = parts
        if int(expires_at) <= time.time():
            return None
        expected = _device_mac(keys[kid], str(self.id), expires_at, nonce)
        return nonce if hmac.compare_digest(mac.encode(), expected.encode()) else None

    def _end_credentials(self) -> None:
        """Move past every generation issued so far; `save` stores it, then the cache must go."""
        secret = self.stored_secret
        secret.generation = max(secret.generation, secret.pending_generation or 0) + 1
        secret.pending_generation = None

    def logout(self) -> None:
        """End every credential issued to this user so far."""
        self._end_credentials()
        self.stored_secret.save()
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
                raise ConflictError(
                    "cannot disable the last enabled operator", code="last-operator"
                )
        self.state = state
        self.save()
        return self

    def reset_password(self, password: str) -> None:
        """Set a one-time password, which the next login must change, and end every credential."""
        if weak(password):
            raise InvalidPasswordError(WEAK, code="password-weak")
        self.password = _hashed(password)
        self.password_changed = 0
        self._end_credentials()
        self.save()
        self._delete("secret")

    def change_password(self, old: str, new: str) -> None:
        """Replace the password and mark it changed, refusing a weak or unchanged one."""
        if weak(new):
            raise InvalidPasswordError(WEAK, code="password-weak")
        if new == old:
            raise InvalidPasswordError(UNCHANGED, code="password-unchanged")
        self.password = _hashed(new)
        self.password_changed = int(time.time())
        self.save()
