import asyncio
import socket
import threading
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from jatszoter_web.app import create_app
from jatszoter_web.cookies import CSRF_COOKIE, LINK_COOKIE, SESSION_COOKIE
from jatszoter_web.settings import Settings

ATTACH = "/api/v1/deployments/00000000-0000-4000-8000-0000000000d1/attach"
HOST = "web.example.com"
ORIGIN = f"https://{HOST}"
COOKIES = f"{LINK_COOKIE}=link-session-alpha; {SESSION_COOKIE}=k1.AAAA; {CSRF_COOKIE}=c"


class Upstream:
    """The API's attach socket on loopback: echoes one frame of each kind, then acts on `case`."""

    def __init__(self) -> None:
        self.headers: list[Headers] = []
        self.loop = asyncio.new_event_loop()
        self.server: Server | None = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        self.ready.wait(5)
        assert self.server is not None
        self.port: int = self.server.sockets[0].getsockname()[1]

    def run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.server = self.loop.run_until_complete(self.start())
        self.ready.set()
        self.loop.run_forever()

    async def start(self) -> Server:
        # serve() reads the running loop as soon as it is called.
        return await serve(
            self.handle, "127.0.0.1", 0, process_request=self.process, compression=None
        )

    def process(self, connection: ServerConnection, request: Request) -> Response | None:
        self.headers.append(request.headers)
        if "case=refuse" in request.path:
            return connection.respond(403, "refused\n")
        return None

    async def handle(self, websocket: ServerConnection) -> None:
        await websocket.send('{"offset": 0}')
        for _ in range(2):
            await websocket.send(await websocket.recv())
        assert websocket.request is not None
        if "case=drop" in websocket.request.path:
            websocket.transport.abort()
            return
        await websocket.close(4403)

    def stop(self) -> None:
        assert self.server is not None
        self.server.close()
        asyncio.run_coroutine_threadsafe(self.server.wait_closed(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


@pytest.fixture
def upstream() -> Iterator[Upstream]:
    server = Upstream()
    yield server
    server.stop()


def client_for(port: int) -> TestClient:
    settings = Settings(
        api_url=f"http://127.0.0.1:{port}",
        cookie_keys={"k1": "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"},
        cookie_kid="k1",
    )
    return TestClient(create_app(settings), base_url=f"https://{HOST}")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def test_a_link_terminal_is_relayed_frame_by_frame_with_its_close_code(
    upstream: Upstream,
) -> None:
    client = client_for(upstream.port)

    with client.websocket_connect(
        f"{ATTACH}?offset=7", headers={"host": HOST, "origin": ORIGIN, "cookie": COOKIES}
    ) as terminal:
        assert terminal.receive_text() == '{"offset": 0}'
        terminal.send_bytes(b"ls\r")
        assert terminal.receive_bytes() == b"ls\r"
        terminal.send_text('{"cols": 80, "rows": 24}')
        assert terminal.receive_text() == '{"cols": 80, "rows": 24}'
        with pytest.raises(WebSocketDisconnect) as closed:
            terminal.receive_bytes()

    assert closed.value.code == 4403
    sent = upstream.headers[0]
    assert sent["host"] == HOST
    assert sent["origin"] == ORIGIN
    assert sent["cookie"] == f"{LINK_COOKIE}=link-session-alpha"
    assert "x-signature" not in sent and "user-agent" not in sent


@pytest.mark.parametrize(("case", "code"), [("refuse", 1008), ("drop", 1011)])
def test_an_api_that_refuses_or_drops_the_socket_closes_the_browser(
    upstream: Upstream, case: str, code: int
) -> None:
    client = client_for(upstream.port)

    with (
        client.websocket_connect(
            f"{ATTACH}?case={case}", headers={"host": HOST, "origin": ORIGIN}
        ) as terminal,
        pytest.raises(WebSocketDisconnect) as closed,
    ):
        if case == "drop":
            terminal.receive_text()
            terminal.send_bytes(b"a")
            terminal.receive_bytes()
            terminal.send_bytes(b"b")
            terminal.receive_bytes()
        terminal.receive_bytes()

    assert closed.value.code == code


def test_an_api_that_cannot_be_reached_closes_the_browser_with_1011() -> None:
    client = client_for(free_port())

    with client.websocket_connect(ATTACH) as terminal, pytest.raises(WebSocketDisconnect) as closed:
        terminal.receive_bytes()

    assert closed.value.code == 1011
