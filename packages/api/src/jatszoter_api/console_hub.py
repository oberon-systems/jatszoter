import asyncio
import uuid
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager, suppress


class Viewer:
    """One attached viewer: bytes queued for it, counted against its cap."""

    def __init__(self, cap: int) -> None:
        self.sent = 0
        self.behind = False
        self._cap = cap
        self._size = 0
        self._chunks: deque[tuple[int, bytes]] = deque()
        self._ready = asyncio.Event()

    def put(self, offset: int, data: bytes) -> None:
        if self.behind:
            return
        if self._size + len(data) > self._cap:
            self._chunks.clear()
            self._size = 0
            self.behind = True
        else:
            self._chunks.append((offset, data))
            self._size += len(data)
        self._ready.set()

    def wake(self) -> None:
        self._ready.set()

    async def take(self, timeout: float) -> tuple[int, bytes] | None:
        """The oldest queued chunk and where it starts; None when `timeout` passes first."""
        if not self._chunks and not self.behind:
            with suppress(TimeoutError):
                await asyncio.wait_for(self._ready.wait(), timeout)
        self._ready.clear()
        if not self._chunks:
            return None
        offset, data = self._chunks.popleft()
        self._size -= len(data)
        return offset, data


# Every socket registers the loop it runs in, because two of them need not share
# one: a viewer's queue belongs to its own loop and is fed through it.
class ConsoleHub:
    def __init__(self, viewer_buffer_bytes: int) -> None:
        self._cap = viewer_buffer_bytes
        self._agents: dict[uuid.UUID, tuple[asyncio.AbstractEventLoop, asyncio.Event]] = {}
        self._viewers: dict[uuid.UUID, set[tuple[asyncio.AbstractEventLoop, Viewer]]] = {}

    @contextmanager
    def agent(self, deployment_id: uuid.UUID) -> Iterator[asyncio.Event]:
        """Hold the deployment's agent socket; the event is set once a newer one replaces it."""
        entry = (asyncio.get_running_loop(), asyncio.Event())
        older = self._agents.get(deployment_id)
        self._agents[deployment_id] = entry
        if older is not None:
            loop, replaced = older
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(replaced.set)
        try:
            yield entry[1]
        finally:
            if self._agents.get(deployment_id) is entry:
                self._agents.pop(deployment_id, None)

    @contextmanager
    def viewer(self, deployment_id: uuid.UUID) -> Iterator[Viewer]:
        entry = (asyncio.get_running_loop(), Viewer(self._cap))
        self._viewers.setdefault(deployment_id, set()).add(entry)
        try:
            yield entry[1]
        finally:
            watching = self._viewers.get(deployment_id, set())
            watching.discard(entry)
            if not watching:
                self._viewers.pop(deployment_id, None)

    def publish(self, deployment_id: uuid.UUID, offset: int, data: bytes) -> None:
        for loop, viewer in list(self._viewers.get(deployment_id, set())):
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(viewer.put, offset, data)
