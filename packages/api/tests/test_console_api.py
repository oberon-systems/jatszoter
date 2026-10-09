import asyncio
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from jatszoter_api.console_hub import Viewer
from jatszoter_api.file import File
from jatszoter_api.lifecycle import DeploymentStatus
from jatszoter_api.models import Agent, Deployment, Image, Role, Scenario, State
from jatszoter_api.models._cache import Cached
from jatszoter_api.models.secret import CREDENTIAL_SECONDS
from jatszoter_api.settings import Settings

S = DeploymentStatus
DEPLOYMENTS = "/api/v1/deployments"
SIZING: dict[str, int] = {
    "cpus": 1,
    "memory_mib": 512,
    "disk_gib": 4,
    "bootstrap_seconds": 600,
    "check_seconds": 30,
    "session_minutes": 60,
}


@pytest.fixture
def settings(settings: Settings, request: pytest.FixtureRequest) -> Settings:
    """The suite's settings, with what a test passes through `indirect` parametrization."""
    return settings.model_copy(update=getattr(request, "param", {}))


def small(**update: int) -> pytest.MarkDecorator:
    return pytest.mark.parametrize("settings", [update], indirect=True)


def headers(path: str, credential: str, key: str, **extra: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    message = "\n".join((timestamp, "GET", path, hashlib.sha256(b"").hexdigest()))
    return {
        "X-Credential": credential,
        "X-Timestamp": timestamp,
        "X-Signature": hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest(),
        **extra,
    }


def frame(offset: int, data: bytes) -> bytes:
    return offset.to_bytes(8, "big") + data


class Shipper:
    """An enabled agent with a live lease, opening console sockets the way the agent does."""

    def __init__(self, api: TestClient, session: Session, settings: Settings) -> None:
        self.api = api
        self.agent = Agent(session=session, name="alpha", state=State.ENABLED)
        self.agent.save()
        credential, self.key = self.agent.issue(
            CREDENTIAL_SECONDS, settings.master_kid, settings.master_keys
        )
        self.credential = f"{credential.kid}:{credential.generation}:{credential.expires_at}"
        self.agent.heartbeat(0)

    def path(self, deployment_id: uuid.UUID) -> str:
        return f"/api/v1/agents/{self.agent.id}/deployments/{deployment_id}/console"

    def connect(self, deployment_id: uuid.UUID, **extra: str) -> WebSocketTestSession:
        path = self.path(deployment_id)
        signed = headers(path, self.credential, self.key, **extra)
        return self.api.websocket_connect(path, headers=signed)


def watch(viewer: Any, deployment_id: uuid.UUID, query: str = "") -> WebSocketTestSession:
    path = f"{DEPLOYMENTS}/{deployment_id}/attach"
    signed = headers(path, viewer.credential, viewer.key, **{"X-Owner": viewer.id})
    session: WebSocketTestSession = viewer.client.websocket_connect(path + query, headers=signed)
    return session


def offset(socket: WebSocketTestSession) -> int:
    answer: int = json.loads(socket.receive_text())["offset"]
    return answer


def ship(socket: WebSocketTestSession, at: int, data: bytes) -> int:
    socket.send_bytes(frame(at, data))
    return offset(socket)


def refused(socket: WebSocketTestSession) -> int:
    with pytest.raises(WebSocketDisconnect) as refusal, socket:
        pass
    return refusal.value.code


def closed(socket: WebSocketTestSession) -> int:
    with pytest.raises(WebSocketDisconnect) as closing:
        socket.receive_bytes()
    return closing.value.code


@pytest.fixture
def scenario(session: Session) -> Scenario:
    Image(session=session, name="debian-12", url="https://example.com/d", digest="a" * 64).save()
    scenario = Scenario(
        session=session,
        slug="nginx-502",
        version=1,
        document={"image": "debian-12"},
        digest="b" * 64,
    )
    scenario.save(File(scenario.bundle_path, b"bundle"))
    return scenario


@pytest.fixture
def shipper(api: TestClient, session: Session, settings: Settings) -> Shipper:
    return Shipper(api, session, settings)


@pytest.fixture
def deploy(session: Session, scenario: Scenario, shipper: Shipper) -> Callable[..., Deployment]:
    def deployed(status: S = S.STARTING, agent_id: uuid.UUID | None = None) -> Deployment:
        deployment = Deployment(
            session=session,
            scenario_id=scenario.id,
            image_id=scenario.image.id,
            agent_id=shipper.agent.id if agent_id is None else agent_id,
            vm_id=uuid.uuid4(),
            status=status,
            **SIZING,
        )
        deployment.save()
        return deployment

    return deployed


@pytest.fixture
def deployment(deploy: Callable[..., Deployment]) -> Deployment:
    return deploy()


def log(deployment: Deployment) -> bytes:
    stored = File(deployment.console.path)
    stored.get()
    return stored.data


def test_an_agent_frame_is_answered_with_the_new_end_and_lands_in_the_log(
    shipper: Shipper, deployment: Deployment
) -> None:
    with shipper.connect(deployment.id) as socket:
        assert offset(socket) == 0
        assert ship(socket, 0, b"alpha") == 5
        assert ship(socket, 3, b"habet") == 8

    assert log(deployment) == b"alphabet"


def test_an_attached_operator_receives_the_offset_then_the_published_bytes(
    shipper: Shipper, operator: Any, deployment: Deployment
) -> None:
    with watch(operator, deployment.id) as viewer, shipper.connect(deployment.id) as agent:
        assert offset(viewer) == 0
        offset(agent)
        ship(agent, 0, b"alpha")

        assert viewer.receive_bytes() == b"alpha"


def test_a_viewer_attaching_later_gets_the_tail(
    shipper: Shipper, operator: Any, deployment: Deployment
) -> None:
    with shipper.connect(deployment.id) as agent:
        offset(agent)
        ship(agent, 0, b"alphabet")

    with watch(operator, deployment.id) as viewer:
        assert offset(viewer) == 0
        assert viewer.receive_bytes() == b"alphabet"


def test_a_viewer_with_an_offset_inside_the_tail_resumes_there(
    shipper: Shipper, operator: Any, deployment: Deployment
) -> None:
    with shipper.connect(deployment.id) as agent:
        offset(agent)
        ship(agent, 0, b"alphabet")

    with watch(operator, deployment.id, "?offset=5") as viewer:
        assert offset(viewer) == 5
        assert viewer.receive_bytes() == b"bet"


def test_a_second_agent_socket_replaces_the_first(shipper: Shipper, deployment: Deployment) -> None:
    with shipper.connect(deployment.id) as first:
        offset(first)
        with shipper.connect(deployment.id) as second:
            offset(second)

            assert closed(first) == 1000
            assert ship(second, 0, b"alpha") == 5


def test_the_download_returns_the_full_log(
    shipper: Shipper, operator: Any, deployment: Deployment
) -> None:
    path = f"{DEPLOYMENTS}/{deployment.id}/console"
    assert operator.request("GET", path).content == b""
    with shipper.connect(deployment.id) as agent:
        offset(agent)
        ship(agent, 0, b"alphabet")

    response = operator.request("GET", path)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.content == b"alphabet"


@pytest.mark.parametrize(
    "extra",
    [{"X-Signature": ""}, {"X-Signature": "0" * 64}, {"X-Credential": "current:1:1"}],
)
def test_an_unsigned_or_wrongly_signed_agent_socket_is_refused(
    shipper: Shipper, deployment: Deployment, extra: dict[str, str]
) -> None:
    assert refused(shipper.connect(deployment.id, **extra)) == 1008


def test_another_agents_deployment_is_refused(
    shipper: Shipper, session: Session, deploy: Callable[..., Deployment]
) -> None:
    other = Agent(session=session, name="beta", state=State.ENABLED)
    other.save()
    foreign = deploy(agent_id=other.id)

    assert refused(shipper.connect(foreign.id)) == 1008


def test_an_agent_without_a_live_lease_is_refused(shipper: Shipper, deployment: Deployment) -> None:
    Cached._client().delete(f"lease:{shipper.agent.id}")

    assert refused(shipper.connect(deployment.id)) == 1008


def test_a_pending_deployment_is_refused(
    shipper: Shipper, deploy: Callable[..., Deployment]
) -> None:
    pending = deploy(status=S.PENDING)

    assert refused(shipper.connect(pending.id)) == 1008


def test_a_trainee_or_unsigned_viewer_is_refused(
    api: TestClient, sign_in: Callable[..., Any], deployment: Deployment
) -> None:
    trainee = sign_in(name="beta", role=Role.TRAINEE)

    assert refused(watch(trainee, deployment.id)) == 1008
    assert refused(api.websocket_connect(f"{DEPLOYMENTS}/{deployment.id}/attach")) == 1008


def test_a_viewer_of_an_unknown_deployment_is_refused(operator: Any) -> None:
    assert refused(watch(operator, uuid.uuid4())) == 1008


@pytest.mark.parametrize("sent", [b"short", frame(0, b"x" * (64 * 1024 + 1))])
def test_a_frame_shorter_than_its_offset_or_over_the_size_closes_with_1009(
    shipper: Shipper, deployment: Deployment, sent: bytes
) -> None:
    with shipper.connect(deployment.id) as agent:
        offset(agent)
        agent.send_bytes(sent)

        assert closed(agent) == 1009


@small(console_limit_bytes=8)
def test_a_full_log_closes_with_4413(shipper: Shipper, deployment: Deployment) -> None:
    with shipper.connect(deployment.id) as agent:
        offset(agent)
        assert ship(agent, 0, b"alphabetical") == 8
        agent.send_bytes(frame(8, b"ical"))

        assert closed(agent) == 4413
    assert log(deployment) == b"alphabet"


@small(console_viewer_buffer_bytes=2)
def test_a_viewer_past_its_cap_is_resent_from_the_tail(
    shipper: Shipper, operator: Any, deployment: Deployment
) -> None:
    with watch(operator, deployment.id) as viewer, shipper.connect(deployment.id) as agent:
        offset(viewer)
        offset(agent)
        ship(agent, 0, b"alphabet")

        assert viewer.receive_bytes() == b"alphabet"


@small(console_viewer_buffer_bytes=2, console_tail_bytes=4)
def test_a_viewer_past_its_cap_closes_with_4429_once_the_tail_lost_its_offset(
    shipper: Shipper, operator: Any, deployment: Deployment
) -> None:
    with watch(operator, deployment.id) as viewer, shipper.connect(deployment.id) as agent:
        offset(viewer)
        offset(agent)
        ship(agent, 0, b"alphabet")

        assert closed(viewer) == 4429


def test_a_viewer_that_never_reads_stays_under_its_cap() -> None:
    async def fill() -> tuple[bool, tuple[int, bytes] | None]:
        viewer = Viewer(8)
        viewer.put(0, b"alpha")
        viewer.put(5, b"bet")
        viewer.put(8, b"a")
        return viewer.behind, await viewer.take(0)

    assert asyncio.run(fill()) == (True, None)


def test_the_download_of_an_unknown_deployment_is_404_and_of_a_trainee_403(
    sign_in: Callable[..., Any], operator: Any, deployment: Deployment
) -> None:
    trainee = sign_in(name="beta", role=Role.TRAINEE)

    assert operator.request("GET", f"{DEPLOYMENTS}/{uuid.uuid4()}/console").status_code == 404
    assert trainee.request("GET", f"{DEPLOYMENTS}/{deployment.id}/console").status_code == 403
