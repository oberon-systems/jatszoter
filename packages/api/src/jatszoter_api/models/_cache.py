import hashlib
import hmac
import logging
import secrets
from collections.abc import Mapping
from typing import Any

import fakeredis
from pydantic_core import from_json, to_json
from valkey import Valkey as CacheClient

log = logging.getLogger(__name__)

_CLIENT: CacheClient | None = None
_KEYS: dict[bytes, bytes] = {}
_KID = b""
_LOCAL_KID = b"local"


def _derived(master: str) -> bytes:
    """The cache's own key, so a leak of it gives no credential key."""
    return hmac.new(master.encode(), b"jatszoter cache", hashlib.sha256).digest()


def _mac(secret: bytes, key: str, payload: bytes) -> bytes:
    """Covers the key too, so a value cannot be moved to another entry."""
    return hmac.new(secret, key.encode() + b"\0" + payload, hashlib.sha256).hexdigest().encode()


def _sealed(key: str, value: Any) -> bytes:
    payload = to_json(value)
    return b"\n".join((_KID, _mac(_KEYS[_KID], key, payload), payload))


def _unsealed(key: str, raw: bytes) -> Any | None:
    """The value of an entry the API wrote; anything else counts as a miss."""
    parts = raw.split(b"\n", 2)
    if len(parts) == 3:
        kid, mac, payload = parts
        secret = _KEYS.get(kid)
        if secret is not None and hmac.compare_digest(mac, _mac(secret, key, payload)):
            return from_json(payload)
    log.warning("refused a cache entry the API did not write under %s", key.partition(":")[0])
    return None


class Cached:
    @staticmethod
    def connect(
        dsn: str | None, master_keys: Mapping[str, str] | None = None, master_kid: str = ""
    ) -> None:
        """Open the cache once at start: Valkey at `dsn`, or an in-memory fake without one."""
        global _CLIENT, _KEYS, _KID
        _CLIENT = fakeredis.FakeValkey() if dsn is None else CacheClient.from_url(dsn)
        _KEYS = {kid.encode(): _derived(master) for kid, master in (master_keys or {}).items()}
        _KID = master_kid.encode()
        if _KID not in _KEYS:
            _KID = _LOCAL_KID
            _KEYS[_KID] = secrets.token_bytes(32)

    @staticmethod
    def _client() -> CacheClient:
        if _CLIENT is None:
            raise RuntimeError("the cache is not connected")
        return _CLIENT

    def _get(self, key: str) -> Any | None:
        raw = self._client().get(key)
        return _unsealed(key, raw) if isinstance(raw, bytes) else None

    def _set(self, key: str, value: Any, ttl: int) -> None:
        self._client().set(key, _sealed(key, value), ex=ttl)

    def _add(self, key: str, value: Any, ttl: int) -> bool:
        """Store `value` only when `key` is free; whether it was."""
        return bool(self._client().set(key, _sealed(key, value), ex=ttl, nx=True))

    def _ttl(self, key: str, ttl: int) -> bool:
        return bool(self._client().expire(key, ttl))

    def _delete(self, key: str) -> bool:
        return bool(self._client().delete(key))

    def _increment(self, key: str, ttl: int) -> int:
        """Count one more under `key`, in a `ttl` window the first count opens."""
        with self._client().pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, ttl, nx=True)
            count, _ = pipe.execute()  # type: ignore[no-untyped-call]  # valkey leaves it untyped
        return count if isinstance(count, int) else 0

    def _decrement(self, key: str) -> None:
        """Take one count back; a counter that reaches zero is dropped."""
        count = self._client().decr(key)
        if not isinstance(count, int) or count <= 0:
            self._client().delete(key)

    def _seconds_left(self, key: str) -> int:
        """Seconds until `key` expires, 0 when it has no TTL or is gone."""
        seconds = self._client().ttl(key)
        return seconds if isinstance(seconds, int) and seconds > 0 else 0
