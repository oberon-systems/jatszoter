import pickle
from typing import Any

import fakeredis
from valkey import Valkey as CacheClient

_CLIENT: CacheClient | None = None


class Cached:
    @staticmethod
    def connect(dsn: str | None) -> None:
        """Open the cache once at start: Valkey at `dsn`, or an in-memory fake without one."""
        global _CLIENT
        _CLIENT = fakeredis.FakeValkey() if dsn is None else CacheClient.from_url(dsn)

    @staticmethod
    def _client() -> CacheClient:
        if _CLIENT is None:
            raise RuntimeError("the cache is not connected")
        return _CLIENT

    def _get(self, key: str) -> Any | None:
        raw = self._client().get(key)
        if not isinstance(raw, bytes):
            return None
        # The cache belongs to the API alone; nothing a client sends is ever unpickled.
        return pickle.loads(raw)  # noqa: S301

    def _set(self, key: str, value: Any, ttl: int) -> None:
        self._client().set(key, pickle.dumps(value), ex=ttl)

    def _ttl(self, key: str, ttl: int) -> bool:
        return bool(self._client().expire(key, ttl))

    def _delete(self, key: str) -> bool:
        return bool(self._client().delete(key))

    def _count(self, key: str) -> int:
        """Read a counter `_increment` wrote: a bare number `_get` cannot unpickle."""
        raw = self._client().get(key)
        return int(raw) if isinstance(raw, bytes) else 0

    def _increment(self, key: str, ttl: int) -> int:
        """Count one more under `key`, in a `ttl` window the first count opens."""
        count = self._client().incr(key)
        self._client().expire(key, ttl, nx=True)
        return count if isinstance(count, int) else 0
