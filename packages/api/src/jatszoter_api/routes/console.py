import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from functools import partial
from typing import Annotated

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
    status,
)
from sqlmodel import Session

from jatszoter_api.auth import socket_agent, socket_link, socket_operator
from jatszoter_api.console_hub import ConsoleHub, Viewer
from jatszoter_api.errors import ConflictError, DomainError, NotFoundError, TooLargeError
from jatszoter_api.lifecycle import TERMINAL, DeploymentStatus
from jatszoter_api.models import Console, Deployment, Link, Role
from jatszoter_api.settings import Settings

OFFSET_BYTES = 8
FRAME_BYTES = 64 * 1024
POLL_SECONDS = 1.0
WS_4403_REVOKED = 4403
WS_4413_TOO_LARGE = 4413
WS_4429_BEHIND = 4429
SHIPPING = frozenset(
    {DeploymentStatus.STARTING, DeploymentStatus.BOOTSTRAPPING, DeploymentStatus.READY}
)

log = logging.getLogger(__name__)

# A socket passes no router dependency built on Request, so each route checks its own caller.
router = APIRouter()


async def _in_session[T](websocket: WebSocket, call: Callable[[Session], T]) -> T:
    def run() -> T:
        with Session(websocket.app.state.engine) as session:
            return call(session)

    return await asyncio.to_thread(run)


async def _refused_unless[T](websocket: WebSocket, call: Callable[[Session], T]) -> T:
    try:
        return await _in_session(websocket, call)
    except (HTTPException, DomainError):
        raise WebSocketException(status.WS_1008_POLICY_VIOLATION) from None


def _filer_url(settings: Settings) -> str | None:
    return None if settings.seaweed_url is None else str(settings.seaweed_url)


async def _send(websocket: WebSocket, data: bytes) -> None:
    for start in range(0, len(data), FRAME_BYTES):
        await websocket.send_bytes(data[start : start + FRAME_BYTES])


async def _offset(websocket: WebSocket, offset: int) -> None:
    await websocket.send_text(json.dumps({"offset": offset}))


async def _next(websocket: WebSocket, replaced: asyncio.Event) -> dict[str, object] | None:
    """The next message from the socket; None once a newer socket replaced this one."""
    receiving = asyncio.ensure_future(websocket.receive())
    replacing = asyncio.ensure_future(replaced.wait())
    done, pending = await asyncio.wait({receiving, replacing}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    return dict(receiving.result()) if receiving in done else None


def _shipping(
    session: Session,
    websocket: WebSocket,
    agent_id: uuid.UUID,
    deployment_id: uuid.UUID,
    settings: Settings,
) -> int:
    """How far the log is held, once the agent may ship this deployment's console."""
    agent = socket_agent(agent_id, websocket, session, settings.master_keys)
    if agent.lease is None:
        raise ConflictError(f"agent {agent.id} holds no live lease")
    try:
        deployment = Deployment(session=session).load(id=deployment_id, agent_id=agent.id)
    except IndexError:
        raise NotFoundError(f"deployment {deployment_id} is not known") from None
    if deployment.status not in SHIPPING:
        raise ConflictError(f"deployment {deployment_id} is {deployment.status}")
    return deployment.console.held(_filer_url(settings))


@router.websocket("/agents/{agent_id}/deployments/{deployment_id}/console")
async def ship(websocket: WebSocket, agent_id: uuid.UUID, deployment_id: uuid.UUID) -> None:
    settings: Settings = websocket.app.state.settings
    hub: ConsoleHub = websocket.app.state.console_hub
    held = await _refused_unless(
        websocket,
        partial(
            _shipping,
            websocket=websocket,
            agent_id=agent_id,
            deployment_id=deployment_id,
            settings=settings,
        ),
    )
    console = Console(deployment_id)
    await websocket.accept()
    try:
        await _offset(websocket, held)
        with hub.agent(deployment_id) as replaced:
            while (message := await _next(websocket, replaced)) is not None:
                if message["type"] == "websocket.disconnect":
                    return
                frame = message.get("bytes")
                if not isinstance(frame, bytes):
                    continue
                if not OFFSET_BYTES <= len(frame) <= OFFSET_BYTES + FRAME_BYTES:
                    await websocket.close(status.WS_1009_MESSAGE_TOO_BIG)
                    return
                offset = int.from_bytes(frame[:OFFSET_BYTES], "big")
                try:
                    start, new = await asyncio.to_thread(
                        console.append,
                        offset,
                        frame[OFFSET_BYTES:],
                        _filer_url(settings),
                        settings.console_tail_bytes,
                        settings.console_limit_bytes,
                    )
                except TooLargeError:
                    await websocket.close(WS_4413_TOO_LARGE)
                    return
                except Exception as error:
                    log.error(
                        "console of deployment %s not stored: %s",
                        deployment_id,
                        type(error).__name__,
                    )
                    await websocket.close(status.WS_1011_INTERNAL_ERROR)
                    return
                if new:
                    hub.publish(deployment_id, start, new)
                await _offset(websocket, start + len(new))
            await websocket.close()
    except WebSocketDisconnect:
        return


def _watching(
    session: Session, websocket: WebSocket, deployment_id: uuid.UUID, settings: Settings
) -> tuple[uuid.UUID | None, Role]:
    """The link the socket came through, None for a signed operator, and the role it watches as."""
    link_id: uuid.UUID | None = None
    role = Role.OPERATOR
    if "x-signature" in websocket.headers:
        socket_operator(websocket, session, settings.master_keys)
    else:
        link, user = socket_link(websocket, session, deployment_id)
        link_id, role = link.id, user.role
    try:
        deployment = Deployment(session=session).load(id=deployment_id)
    except IndexError:
        raise NotFoundError(f"deployment {deployment_id} is not known") from None
    if link_id is not None and deployment.status is not DeploymentStatus.READY:
        raise ConflictError(f"deployment {deployment_id} is {deployment.status}")
    return link_id, role


def _revoked(session: Session, link_id: uuid.UUID) -> bool:
    try:
        return not Link(session=session).load(id=link_id).live
    except IndexError:
        return True


def _ended(session: Session, deployment_id: uuid.UUID) -> bool:
    try:
        return Deployment(session=session).load(id=deployment_id).status in TERMINAL
    except IndexError:
        return True


async def _drop_frames(websocket: WebSocket, viewer: Viewer) -> None:
    """Read and drop what the viewer sends until it leaves, then wake its sender."""
    try:
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass
    finally:
        viewer.wake()


@router.websocket("/deployments/{deployment_id}/attach")
async def attach(
    websocket: WebSocket,
    deployment_id: uuid.UUID,
    offset: Annotated[int | None, Query(ge=0)] = None,
) -> None:
    settings: Settings = websocket.app.state.settings
    hub: ConsoleHub = websocket.app.state.console_hub
    link_id, role = await _refused_unless(
        websocket,
        partial(_watching, websocket=websocket, deployment_id=deployment_id, settings=settings),
    )
    console = Console(deployment_id)
    await websocket.accept()
    # Registered before the tail is read, so nothing published meanwhile is lost.
    with hub.viewer(deployment_id, link_id) as viewer:
        listener = asyncio.ensure_future(_drop_frames(websocket, viewer))
        try:
            # A revoke between the check above and the registration never reached the hub.
            if link_id is not None and await _in_session(
                websocket, partial(_revoked, link_id=link_id)
            ):
                await websocket.close(WS_4403_REVOKED)
                return
            floor = None
            if role is not Role.OPERATOR:
                floor = await asyncio.to_thread(console.ready)
                if floor is None:
                    floor = await asyncio.to_thread(console.held, _filer_url(settings))
                offset = floor if offset is None else max(offset, floor)
            resumed = None if offset is None else await asyncio.to_thread(console.since, offset)
            if offset is not None and resumed is not None:
                start, data = offset, resumed
            else:
                start, data = await asyncio.to_thread(console.tail)
                if floor is not None and start < floor:
                    start, data = floor, b""
            await _offset(websocket, start)
            await _send(websocket, data)
            viewer.sent = start + len(data)
            while not listener.done():
                if viewer.revoked:
                    await websocket.close(WS_4403_REVOKED)
                    return
                if viewer.behind:
                    viewer.behind = False
                    missed = await asyncio.to_thread(console.since, viewer.sent)
                    if missed is None:
                        await websocket.close(WS_4429_BEHIND)
                        return
                    await _send(websocket, missed)
                    viewer.sent += len(missed)
                    continue
                chunk = await viewer.take(POLL_SECONDS)
                if chunk is None:
                    ended = partial(_ended, deployment_id=deployment_id)
                    if (
                        not listener.done()
                        and not viewer.revoked
                        and await _in_session(websocket, ended)
                    ):
                        await websocket.close(status.WS_1000_NORMAL_CLOSURE)
                        return
                    continue
                start, data = chunk
                if start + len(data) <= viewer.sent:
                    continue
                if start > viewer.sent:
                    await _offset(websocket, start)
                else:
                    data = data[viewer.sent - start :]
                await _send(websocket, data)
                viewer.sent = start + len(data)
        except WebSocketDisconnect:
            return
        finally:
            listener.cancel()
