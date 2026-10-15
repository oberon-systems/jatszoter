import asyncio
import contextlib
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, WebSocket, status
from fastapi.responses import JSONResponse, Response
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, InvalidURI
from websockets.typing import Origin

from jatszoter_web.api import TIMEOUT_SECONDS, Api, ApiDown
from jatszoter_web.cookies import LINK_COOKIE
from jatszoter_web.csrf import check_browser
from jatszoter_web.settings import Settings

BODY_LIMIT = 64 * 1024
SEGMENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*")
PASSED_ON = ("origin", "content-type", "accept")
PASSED_BACK = ("content-type", "location", "cache-control", "referrer-policy")
# What the browser gets when the API's socket ends without a code it could be given.
DROPPED = 1011
UNCODED = {1005: 1000, 1006: DROPPED}
GATEWAY = {502: "the API cannot be reached", 504: "the API did not answer in time"}

router = APIRouter()


def relayed(rest: str) -> bool:
    """Whether every segment below the deployment is a plain name, so no `..` climbs out."""
    return all(SEGMENT.fullmatch(segment) for segment in rest.split("/"))


def link_cookie(cookies: dict[str, str]) -> str | None:
    token = cookies.get(LINK_COOKIE)
    return f"{LINK_COOKIE}={token}" if token else None


async def bounded_body(request: Request) -> bytes:
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > BODY_LIMIT:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="too large")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > BODY_LIMIT:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="too large")
    return bytes(body)


async def relay(request: Request) -> Response:
    """The request passed to the API unsigned, with the allow-listed headers alone."""
    api: Api = request.app.state.api
    body = await bounded_body(request)
    headers = [("host", request.headers.get("host", ""))]
    headers += [(name, request.headers[name]) for name in PASSED_ON if name in request.headers]
    cookie = link_cookie(request.cookies)
    if cookie:
        headers.append(("cookie", cookie))
    query = request.url.query
    url = request.url.path + (f"?{query}" if query else "")
    try:
        answer = await api.send(
            api.http.build_request(request.method, url, content=body, headers=headers)
        )
    except ApiDown as down:
        return JSONResponse({"detail": GATEWAY[down.status]}, status_code=down.status)
    response = Response(answer.content, status_code=answer.status_code)
    for name in PASSED_BACK:
        if name in answer.headers:
            response.headers[name] = answer.headers[name]
    for value in answer.headers.get_list("set-cookie"):
        response.raw_headers.append((b"set-cookie", value.encode("latin-1")))
    return response


@router.get("/s/{token}")
async def open_link(request: Request, token: str) -> Response:
    if not SEGMENT.fullmatch(token):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return await relay(request)


@router.api_route("/api/v1/deployments/{deployment_id:uuid}/{rest:path}", methods=["GET", "POST"])
async def link_call(request: Request, rest: str) -> Response:
    if not relayed(rest):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if request.method == "POST":
        await check_browser(request, form=False)
    return await relay(request)


def upstream_uri(settings: Settings, host: str, path: str, query: str) -> str:
    """The API's socket URI under the browser's own `Host`, which the API's `Origin` check reads."""
    base = urlsplit(settings.api_base)
    scheme = "wss" if base.scheme == "https" else "ws"
    return f"{scheme}://{host}{base.path.rstrip('/')}{path}" + (f"?{query}" if query else "")


async def bridge(
    websocket: WebSocket,
    headers: dict[str, str],
    origin: str | None,
) -> None:
    """Open the API's socket, then pass frames both ways, one at a time, close codes included."""
    settings: Settings = websocket.app.state.settings
    target = settings.api_url
    secure = target.scheme == "https"
    try:
        uri = upstream_uri(
            settings, websocket.headers.get("host", ""), websocket.url.path, websocket.url.query
        )
        upstream = await connect(
            uri,
            host=target.host,
            port=target.port,
            origin=Origin(origin) if origin else None,
            additional_headers=headers,
            user_agent_header=None,
            compression=None,
            proxy=None,
            max_queue=1,
            open_timeout=TIMEOUT_SECONDS,
            ssl=True if secure else None,
            server_hostname=target.host if secure else None,
        )
    except (InvalidStatus, InvalidURI):
        await refuse(websocket, status.WS_1008_POLICY_VIOLATION)
        return
    except Exception:
        await refuse(websocket, DROPPED)
        return
    async with upstream:
        await websocket.accept()
        await pump(websocket, upstream)


async def refuse(websocket: WebSocket, code: int) -> None:
    await websocket.accept()
    await websocket.close(code)


async def pump(websocket: WebSocket, upstream: ClientConnection) -> None:
    async def down() -> None:
        with contextlib.suppress(ConnectionClosed):
            async for frame in upstream:
                if isinstance(frame, str):
                    await websocket.send_text(frame)
                else:
                    await websocket.send_bytes(frame)

    async def up() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            frame = message.get("bytes")
            if frame is None:
                frame = message.get("text")
            if frame is not None:
                await upstream.send(frame)

    tasks = {asyncio.create_task(down(), name="down"), asyncio.create_task(up(), name="up")}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    if any(task.get_name() == "down" for task in done) or upstream.close_code is not None:
        await upstream.wait_closed()
        code = upstream.close_code or DROPPED
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(UNCODED.get(code, code))
    else:
        await upstream.close()


@router.websocket("/api/v1/deployments/{deployment_id:uuid}/attach")
async def link_socket(websocket: WebSocket) -> None:
    cookie = link_cookie(websocket.cookies)
    await bridge(websocket, {"cookie": cookie} if cookie else {}, websocket.headers.get("origin"))
