import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from functools import partial
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
    status,
)
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session
from starlette.types import Message
from valkey.exceptions import ValkeyError

from jatszoter_api.auth import (
    require_deployment_operator,
    socket_agent,
    socket_link,
    socket_operator,
)
from jatszoter_api.console_hub import ConsoleHub, Viewer
from jatszoter_api.errors import ConflictError, DomainError, NotFoundError, TooLargeError
from jatszoter_api.lifecycle import TERMINAL, DeploymentStatus
from jatszoter_api.models import Console, Deployment, Link, Recording, Role
from jatszoter_api.models.console import Holder
from jatszoter_api.models.recording import live
from jatszoter_api.routes.deps import SessionDep, SettingsDep
from jatszoter_api.settings import Settings

OFFSET_BYTES = 8
FRAME_BYTES = 64 * 1024
KEYS_BYTES = 4096
SIZES = range(1, 501)
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


def _recorded(
    engine: Engine, deployment_id: uuid.UUID, write: Callable[..., None], *args: Any
) -> None:
    """Hand the deployment's live recording to `write`; a failure is logged and goes no further."""
    try:
        recording_id = live(deployment_id)
        if recording_id is None:
            return
        with Session(engine) as session:
            write(Recording(session=session).load(id=recording_id), *args)
    except Exception as error:
        log.error("recording of deployment %s not written: %s", deployment_id, type(error).__name__)


def _stored(
    engine: Engine, console: Console, offset: int, data: bytes, settings: Settings
) -> tuple[int, bytes]:
    """Store a frame and record what was kept of it; answer where that starts and the bytes."""
    filer_url = _filer_url(settings)
    start, new = console.append(
        offset, data, filer_url, settings.console_tail_bytes, settings.console_limit_bytes
    )
    if new:
        _recorded(
            engine,
            console.deployment_id,
            Recording.output,
            start,
            new,
            console,
            filer_url,
            settings.recording_limit_bytes,
        )
    return start, new


async def _send(websocket: WebSocket, data: bytes) -> None:
    for start in range(0, len(data), FRAME_BYTES):
        await websocket.send_bytes(data[start : start + FRAME_BYTES])


async def _offset(websocket: WebSocket, offset: int) -> None:
    await websocket.send_text(json.dumps({"offset": offset}))


async def _control(websocket: WebSocket, holder: Holder) -> None:
    await websocket.send_text(json.dumps({"control": holder.value}))


async def _next(receiving: asyncio.Future[Message], woken: asyncio.Event) -> bool:
    """Wait for the socket's next message or a wake; True once the message is there."""
    waking = asyncio.ensure_future(woken.wait())
    either: set[asyncio.Future[Any]] = {receiving, waking}
    await asyncio.wait(either, return_when=asyncio.FIRST_COMPLETED)
    waking.cancel()
    return receiving.done()


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
        with hub.agent(deployment_id) as downlink:
            # One receive lives across wakes: cancelling it could lose a frame.
            receiving = asyncio.ensure_future(websocket.receive())
            try:
                while True:
                    arrived = await _next(receiving, downlink.woken)
                    if downlink.replaced:
                        break
                    for waiting in downlink.take():
                        if isinstance(waiting, bytes):
                            await websocket.send_bytes(waiting)
                        else:
                            await websocket.send_text(waiting)
                    if not arrived:
                        continue
                    message = receiving.result()
                    if message["type"] == "websocket.disconnect":
                        return
                    receiving = asyncio.ensure_future(websocket.receive())
                    frame = message.get("bytes")
                    if not isinstance(frame, bytes):
                        continue
                    if not OFFSET_BYTES <= len(frame) <= OFFSET_BYTES + FRAME_BYTES:
                        await websocket.close(status.WS_1009_MESSAGE_TOO_BIG)
                        return
                    offset = int.from_bytes(frame[:OFFSET_BYTES], "big")
                    try:
                        start, new = await asyncio.to_thread(
                            _stored,
                            websocket.app.state.engine,
                            console,
                            offset,
                            frame[OFFSET_BYTES:],
                            settings,
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
            finally:
                receiving.cancel()
            await websocket.close()
    except WebSocketDisconnect:
        return


def _watching(
    session: Session, websocket: WebSocket, deployment_id: uuid.UUID, settings: Settings
) -> tuple[uuid.UUID | None, Role]:
    """The link the socket came through, None for a signed operator, and the role it watches as."""
    link: Link | None = None
    role = Role.OPERATOR
    if "x-signature" in websocket.headers:
        socket_operator(websocket, session, settings.master_keys)
    else:
        link, user = socket_link(websocket, session, deployment_id)
        role = user.role
    try:
        deployment = Deployment(session=session).load(id=deployment_id)
    except IndexError:
        raise NotFoundError(f"deployment {deployment_id} is not known") from None
    if link is None:
        return None, role
    if deployment.status is not DeploymentStatus.READY:
        raise ConflictError(f"deployment {deployment_id} is {deployment.status}")
    if role is Role.TRAINEE:
        _resumed(link, deployment.console, _filer_url(settings))
    return link.id, role


def _resumed(link: Link, console: Console, filer_url: str | None) -> None:
    """Name the link's open recording live again once the cache lost it."""
    try:
        recording = link.recording
        if not recording.size_bytes and live(link.deployment_id) is None:
            recording.resume(console, filer_url)
    except IndexError:
        return
    except Exception as error:
        log.error(
            "recording of deployment %s not resumed: %s", link.deployment_id, type(error).__name__
        )


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


def _resize(text: str) -> str | None:
    """The resize a text frame asks for, as the frame the agent gets; None when it is not one."""
    try:
        asked = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(asked, dict):
        return None
    cols, rows = asked.get("cols"), asked.get("rows")
    # A bool is an int to isinstance, and no size.
    if type(cols) is not int or type(rows) is not int or cols not in SIZES or rows not in SIZES:
        return None
    return json.dumps({"cols": cols, "rows": rows})


async def _forward(
    websocket: WebSocket, viewer: Viewer, hub: ConsoleHub, console: Console, role: Role
) -> int | None:
    """Pass the holder's keys and resizes to the agent until the viewer leaves, then wake its
    sender; the answer is the code to close an oversized frame's socket with."""
    settings: Settings = websocket.app.state.settings
    try:
        while (message := await websocket.receive())["type"] != "websocket.disconnect":
            keys, text = message.get("bytes"), message.get("text")
            sent: bytes | str | None = None
            if isinstance(keys, bytes):
                if len(keys) > KEYS_BYTES:
                    return status.WS_1009_MESSAGE_TOO_BIG
                sent = keys
            elif isinstance(text, str):
                sent = _resize(text)
            if not sent or role is Role.VIEWER:
                continue
            try:
                holder = await asyncio.to_thread(console.control)
            except ValkeyError:
                continue
            if role.value != holder.value or not hub.to_agent(console.deployment_id, sent):
                continue
            if isinstance(sent, str):
                size = json.loads(sent)
                await asyncio.to_thread(
                    _recorded,
                    websocket.app.state.engine,
                    console.deployment_id,
                    Recording.resize,
                    size["cols"],
                    size["rows"],
                    _filer_url(settings),
                    settings.recording_limit_bytes,
                )
        return None
    finally:
        viewer.wake()


class ControlIn(BaseModel):
    holder: Holder


@router.post(
    "/deployments/{deployment_id}/control", dependencies=[Depends(require_deployment_operator)]
)
def control(
    body: ControlIn,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    deployment_id: uuid.UUID,
) -> ControlIn:
    try:
        deployment = Deployment(session=session).load(id=deployment_id)
    except IndexError:
        raise NotFoundError(f"deployment {deployment_id} is not known") from None
    if deployment.status is not DeploymentStatus.READY:
        raise ConflictError(f"deployment {deployment_id} is {deployment.status}")
    changed = deployment.console.control() is not body.holder
    deployment.console.give(body.holder)
    if changed:
        _recorded(
            request.app.state.engine,
            deployment_id,
            Recording.mark,
            f"control {body.holder.value}",
            _filer_url(settings),
            settings.recording_limit_bytes,
        )
    request.app.state.console_hub.announce(deployment_id, body.holder)
    return body


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
        listener = asyncio.ensure_future(_forward(websocket, viewer, hub, console, role))
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
            await _control(websocket, await asyncio.to_thread(console.control))
            await _send(websocket, data)
            viewer.sent = start + len(data)
            while not listener.done():
                if viewer.revoked:
                    await websocket.close(WS_4403_REVOKED)
                    return
                if viewer.control is not None:
                    holder, viewer.control = viewer.control, None
                    await _control(websocket, holder)
                    continue
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
            if listener.exception() is None and (code := listener.result()) is not None:
                await websocket.close(code)
        except WebSocketDisconnect:
            return
        finally:
            listener.cancel()
