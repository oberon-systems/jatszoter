import asyncio
import uuid
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager, suppress

from jatszoter_api.models.console import Holder

QUEUE_MESSAGES = 256


class Viewer:
    """One attached viewer: bytes queued for it, counted against its cap."""

    def __init__(self, cap: int) -> None:
        self.sent = 0
        self.behind = False
        self.revoked = False
        self.control: Holder | None = None
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

    def revoke(self) -> None:
        self.revoked = True
        self._ready.set()

    def announce(self, holder: Holder) -> None:
        self.control = holder
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


class AgentSocket:
    """One agent socket: the keys and resizes waiting to go down it."""

    def __init__(self) -> None:
        self.replaced = False
        self.woken = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        self._waiting: deque[bytes | str] = deque()

    def _wake(self) -> None:
        with suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self.woken.set)

    def replace(self) -> None:
        self.replaced = True
        self._wake()

    def put(self, message: bytes | str) -> bool:
        if len(self._waiting) >= QUEUE_MESSAGES:
            return False
        self._waiting.append(message)
        self._wake()
        return True

    def take(self) -> list[bytes | str]:
        """Everything that waits, oldest first; the wake is spent."""
        self.woken.clear()
        taken = list(self._waiting)
        self._waiting.clear()
        return taken


# Every socket registers the loop it runs in, because two of them need not share
# one: a viewer's queue belongs to its own loop and is fed through it.
class ConsoleHub:
    def __init__(self, viewer_buffer_bytes: int) -> None:
        self._cap = viewer_buffer_bytes
        self._agents: dict[uuid.UUID, AgentSocket] = {}
        self._viewers: dict[uuid.UUID, set[tuple[asyncio.AbstractEventLoop, Viewer]]] = {}
        self._links: dict[uuid.UUID, set[tuple[asyncio.AbstractEventLoop, Viewer]]] = {}

    @contextmanager
    def agent(self, deployment_id: uuid.UUID) -> Iterator[AgentSocket]:
        """Hold the deployment's agent socket; it is woken as replaced once a newer one comes."""
        entry = AgentSocket()
        older = self._agents.get(deployment_id)
        self._agents[deployment_id] = entry
        if older is not None:
            older.replace()
        try:
            yield entry
        finally:
            if self._agents.get(deployment_id) is entry:
                self._agents.pop(deployment_id, None)

    def to_agent(self, deployment_id: uuid.UUID, message: bytes | str) -> bool:
        """Queue a message for the deployment's agent socket; False without one or when full."""
        entry = self._agents.get(deployment_id)
        return entry is not None and entry.put(message)

    @contextmanager
    def viewer(
        self, deployment_id: uuid.UUID, link_id: uuid.UUID | None = None
    ) -> Iterator[Viewer]:
        entry = (asyncio.get_running_loop(), Viewer(self._cap))
        held = [(self._viewers, deployment_id)]
        if link_id is not None:
            held.append((self._links, link_id))
        for index, key in held:
            index.setdefault(key, set()).add(entry)
        try:
            yield entry[1]
        finally:
            for index, key in held:
                watching = index.get(key, set())
                watching.discard(entry)
                if not watching:
                    index.pop(key, None)

    def publish(self, deployment_id: uuid.UUID, offset: int, data: bytes) -> None:
        for loop, viewer in list(self._viewers.get(deployment_id, set())):
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(viewer.put, offset, data)

    def revoke(self, link_id: uuid.UUID) -> None:
        """Mark every viewer attached through this link as revoked and wake it."""
        for loop, viewer in list(self._links.get(link_id, set())):
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(viewer.revoke)

    def announce(self, deployment_id: uuid.UUID, holder: Holder) -> None:
        """Hand the holder of control to every viewer of the deployment and wake it."""
        for loop, viewer in list(self._viewers.get(deployment_id, set())):
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(viewer.announce, holder)
