import uuid

from valkey.client import Pipeline

from jatszoter_api.errors import TooLargeError
from jatszoter_api.file import File
from jatszoter_api.models._cache import Cached

TTL = 24 * 60 * 60


def _commit(pipe: Pipeline) -> None:
    pipe.execute()  # type: ignore[no-untyped-call]  # valkey leaves it untyped


class Console(Cached):
    """A deployment's console: the last bytes in the cache, every byte in the file store."""

    def __init__(self, deployment_id: uuid.UUID) -> None:
        self.deployment_id = deployment_id
        self.path = f"consoles/{deployment_id}.log"

    def _key(self, name: str) -> str:
        return f"console:{self.deployment_id}:{name}"

    def _number(self, name: str) -> int | None:
        raw = self._client().get(self._key(name))
        return int(raw) if isinstance(raw, bytes) else None

    def held(self, filer_url: str | None) -> int:
        """How far the log is held: the tail's end, or the full log's size once the tail is lost."""
        end = self._number("end")
        if end is not None:
            return end
        end = File(self.path, filer_url=filer_url).size()
        pipe = self._client().pipeline()
        pipe.set(self._key("tail"), b"", ex=TTL)
        pipe.set(self._key("start"), end, ex=TTL)
        pipe.set(self._key("end"), end, ex=TTL)
        _commit(pipe)
        return end

    def append(
        self, offset: int, data: bytes, filer_url: str | None, tail_bytes: int, limit_bytes: int
    ) -> tuple[int, bytes]:
        """Keep the part of `data`, sent from `offset`, past what is held; answer where it starts
        and the bytes kept."""
        held = self.held(filer_url)
        if offset > held:
            return held, b""
        new = data[held - offset :]
        if not new:
            return held, b""
        if held >= limit_bytes:
            raise TooLargeError(
                f"console of deployment {self.deployment_id} is at its {limit_bytes} byte limit"
            )
        new = new[: limit_bytes - held]
        File(self.path, new, filer_url).append()

        start = self._number("start")
        size = 0 if start is None else held - start
        client = self._client()
        pipe = client.pipeline()
        if size + len(new) <= tail_bytes:
            pipe.append(self._key("tail"), new)
            pipe.expire(self._key("tail"), TTL)
        else:
            keep = tail_bytes - len(new)
            raw = client.getrange(self._key("tail"), size - keep, -1) if keep > 0 else b""
            head = raw if isinstance(raw, bytes) else b""
            pipe.set(self._key("tail"), head + new[-tail_bytes:], ex=TTL)
        end = held + len(new)
        pipe.set(self._key("start"), end - min(size + len(new), tail_bytes), ex=TTL)
        pipe.set(self._key("end"), end, ex=TTL)
        pipe.expire(self._key("ready"), TTL)
        _commit(pipe)
        return held, new

    def _read(self) -> tuple[int | None, bytes]:
        values = self._client().mget(self._key("start"), self._key("tail"))
        start, tail = values if isinstance(values, list) else (None, None)
        return (
            int(start) if isinstance(start, bytes) else None,
            tail if isinstance(tail, bytes) else b"",
        )

    def since(self, offset: int) -> bytes | None:
        """The tail from `offset` on; None once `offset` fell out of it."""
        start, tail = self._read()
        if start is None or offset < start:
            return None
        return tail[offset - start :]

    def tail(self) -> tuple[int, bytes]:
        start, tail = self._read()
        return start or 0, tail

    def mark_ready(self, filer_url: str | None) -> None:
        self._client().set(self._key("ready"), self.held(filer_url), ex=TTL)

    def ready(self) -> int | None:
        return self._number("ready")

    def drop(self) -> None:
        self._client().delete(*(self._key(name) for name in ("tail", "start", "end", "ready")))
