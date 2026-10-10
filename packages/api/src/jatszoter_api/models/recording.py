import codecs
import json
import threading
import time
import uuid
from typing import Any, Self

from sqlmodel import Field, Session

from jatszoter_api.file import File
from jatszoter_api.models._base import Base
from jatszoter_api.models._cache import Cached
from jatszoter_api.models.console import Console

TTL = 24 * 60 * 60
WIDTH, HEIGHT = 80, 24
FULL = "recording limit reached"

# One event is stamped and appended at a time, so the times in a file never go back.
_LOCK = threading.RLock()


def _of(deployment_id: uuid.UUID) -> str:
    return f"recording:of:{deployment_id}"


def live(deployment_id: uuid.UUID) -> uuid.UUID | None:
    """The id of the deployment's live recording; None without one."""
    raw = Cached._client().get(_of(deployment_id))
    return uuid.UUID(raw.decode()) if isinstance(raw, bytes) else None


class Recording(Base, table=True):
    __tablename__ = "recordings"

    deployment_id: uuid.UUID = Field(index=True)
    link_id: uuid.UUID = Field(unique=True)
    url: str = ""
    size_bytes: int = 0
    duration_seconds: float = 0.0

    def __init__(self, session: Session, **data: Any) -> None:
        super().__init__(session, **data)

    @property
    def path(self) -> str:
        return f"recordings/{self.deployment_id}/{self.id}.cast"

    def load(self, **key: Any) -> Self:
        return self._load(self._session, **key)[0]

    def save(self) -> None:
        self._save()

    def _key(self, name: str) -> str:
        return f"recording:{self.id}:{name}"

    def _number(self, name: str) -> int | None:
        raw = self._client().get(self._key(name))
        return int(raw) if isinstance(raw, bytes) else None

    def _name(self) -> None:
        self._client().set(_of(self.deployment_id), str(self.id), ex=TTL)

    def _append(self, line: bytes, size: int, filer_url: str | None) -> None:
        File(self.path, line, filer_url).append()
        client = self._client()
        client.set(self._key("size"), size + len(line), ex=TTL)
        for name in ("offset", "rest"):
            client.expire(self._key(name), TTL)
        client.expire(_of(self.deployment_id), TTL)

    def _event(
        self, code: str, text: str, filer_url: str | None, limit_bytes: int, at: float | None = None
    ) -> None:
        """Append one event, or the limit marker in its place; nothing once the limit was hit."""
        with _LOCK:
            client = self._client()
            if isinstance(client.get(self._key("full")), bytes):
                return
            size = self._number("size")
            if size is None:
                size = File(self.path, filer_url=filer_url).size()
            stamp = max(time.time() - self.created_at, 0.0) if at is None else at
            line = json.dumps([round(stamp, 6), code, text]).encode() + b"\n"
            if size + len(line) <= limit_bytes:
                self._append(line, size, filer_url)
                return
            line = json.dumps([round(stamp, 6), "m", FULL]).encode() + b"\n"
            self._append(line, size, filer_url)
            client.set(self._key("full"), 1, ex=TTL)

    def start(self, console: Console, title: str, filer_url: str | None, limit_bytes: int) -> None:
        """Store the row and the header, then take what the console holds since READY."""
        self.url = File(self.path, filer_url=filer_url).url
        self.save()
        header = {
            "version": 2,
            "width": WIDTH,
            "height": HEIGHT,
            "timestamp": self.created_at,
            "title": title,
        }
        with _LOCK:
            self._append(json.dumps(header).encode() + b"\n", 0, filer_url)
            ready = console.ready()
            if ready is not None:
                seen = console.since(ready)
                if seen is None:
                    self._recorded(console.held(filer_url), b"")
                else:
                    self._output(ready, seen, filer_url, limit_bytes, at=0.0)
            self._name()

    def resume(self, console: Console, filer_url: str | None) -> None:
        """Name this the live recording again after the cache lost it, from the log end on."""
        with _LOCK:
            if self._number("offset") is None:
                self._recorded(console.held(filer_url), b"")
            self._name()

    def _recorded(self, offset: int, rest: bytes) -> None:
        client = self._client()
        client.set(self._key("offset"), offset, ex=TTL)
        client.set(self._key("rest"), rest, ex=TTL)

    def _output(
        self,
        offset: int,
        data: bytes,
        filer_url: str | None,
        limit_bytes: int,
        at: float | None = None,
    ) -> None:
        raw = self._client().get(self._key("rest"))
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        text = decoder.decode((raw if isinstance(raw, bytes) else b"") + data)
        if text:
            self._event("o", text, filer_url, limit_bytes, at)
        self._recorded(offset + len(data), decoder.getstate()[0])

    def output(
        self, offset: int, data: bytes, console: Console, filer_url: str | None, limit_bytes: int
    ) -> None:
        """Record the part of `data`, stored at `offset`, past what is recorded already."""
        with _LOCK:
            recorded = self._number("offset")
            if recorded is None:
                recorded = console.ready()
            if recorded is None or offset + len(data) <= recorded:
                return
            if offset > recorded:
                # A frame was missed: the tail still has it, or the recording skips ahead.
                missed = console.since(recorded)
                if missed is not None:
                    offset, data = recorded, missed
            else:
                offset, data = recorded, data[recorded - offset :]
            self._output(offset, data, filer_url, limit_bytes)

    def resize(self, cols: int, rows: int, filer_url: str | None, limit_bytes: int) -> None:
        self._event("r", f"{cols}x{rows}", filer_url, limit_bytes)

    def mark(self, text: str, filer_url: str | None, limit_bytes: int) -> None:
        self._event("m", text, filer_url, limit_bytes)

    def close(self, filer_url: str | None) -> None:
        """Store the file's size and the duration once; a repeated call changes nothing."""
        if self.size_bytes:
            return
        with _LOCK:
            self.size_bytes = File(self.path, filer_url=filer_url).size()
            self.duration_seconds = round(max(time.time() - self.created_at, 0.0), 3)
            self.save()
            client = self._client()
            client.delete(*(self._key(name) for name in ("offset", "size", "rest", "full")))
            if live(self.deployment_id) == self.id:
                client.delete(_of(self.deployment_id))
