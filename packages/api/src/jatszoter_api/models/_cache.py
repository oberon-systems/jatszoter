import functools
import pickle
from collections.abc import Callable
from typing import Any

import fakeredis
from valkey import Valkey

from jatszoter_api.settings import get_settings


@functools.cache
def cache_client() -> Valkey:
    dsn = get_settings().valkey_dsn
    if dsn is None:
        return fakeredis.FakeValkey()
    return Valkey.from_url(str(dsn))


def cached(
    *, key: Callable[[Any], str] | None = None, ttl: int = 10
) -> Callable[[Callable[[Any], Any]], property]:
    """A property served from the cache for `ttl` seconds, renewed on every hit.

    `key` names the entry from the object, its `id` by default; `del obj.<name>` drops it.
    """

    def decorate(func: Callable[[Any], Any]) -> property:
        def name(obj: Any) -> str:
            return key(obj) if key is not None else str(obj.id)

        def load(obj: Any) -> Any:
            client, entry = cache_client(), name(obj)
            raw = client.get(entry)
            if isinstance(raw, bytes):
                client.expire(entry, ttl)
                # The cache belongs to the API alone; nothing a client sends is ever unpickled.
                return pickle.loads(raw)  # noqa: S301
            value = func(obj)
            client.set(entry, pickle.dumps(value), ex=ttl)
            return value

        def forget(obj: Any) -> None:
            cache_client().delete(name(obj))

        return property(load, None, forget, func.__doc__)

    return decorate
