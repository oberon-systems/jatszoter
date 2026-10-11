import asyncio
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from jatszoter_api.console_hub import ConsoleHub, Viewer
from jatszoter_api.file import File
from jatszoter_api.lifecycle import Actor, DeploymentStatus
from jatszoter_api.models import (
    Agent,
    Deployment,
    Image,
    Link,
    Recording,
    Role,
    Scenario,
    State,
    User,
)
from jatszoter_api.models._cache import Cached
from jatszoter_api.models.console import Holder
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


def opened(
    api: TestClient, session: Session, deployment: Deployment, role: Role, name: str = "beta"
) -> tuple[Link, str]:
    """A link of a new user of `role`, opened: the link and its session cookie."""
    owner = User(session=session, name=name, role=role, state=State.ENABLED)
    owner.save()
    link = Link(session=session, deployment_id=deployment.id, user_id=owner.id)
    link.save()
    response = api.get(f"/s/{link.token}", follow_redirects=False)
    assert response.status_code == 303
    return link, response.headers["set-cookie"].partition(";")[0]


def join(
    api: TestClient, deployment_id: uuid.UUID, cookie: str, query: str = "", **extra: str
) -> WebSocketTestSession:
    path = f"{DEPLOYMENTS}/{deployment_id}/attach{query}"
    session: WebSocketTestSession = api.websocket_connect(path, headers={"Cookie": cookie, **extra})
    return session


def offset(socket: WebSocketTestSession) -> int:
    answer: int = json.loads(socket.receive_text())["offset"]
    return answer


def control(socket: WebSocketTestSession) -> str:
    holder: str = json.loads(socket.receive_text())["control"]
    return holder


def attached(socket: WebSocketTestSession) -> int:
    """A viewer's first offset, with the holder it hears next read away."""
    at = offset(socket)
    control(socket)
    return at


def hand(caller: Any, deployment_id: uuid.UUID, holder: str) -> httpx.Response:
    """A signed request that gives control to `holder`."""
    path = f"{DEPLOYMENTS}/{deployment_id}/control"
    answer: httpx.Response = caller.request("POST", path, json={"holder": holder})
    return answer


def ask(
    api: TestClient, deployment_id: uuid.UUID, cookie: str, holder: str = "operator", **extra: str
) -> httpx.Response:
    """A link's request, by its cookie, that gives control to `holder`."""
    answer: httpx.Response = api.post(
        f"{DEPLOYMENTS}/{deployment_id}/control",
        json={"holder": holder},
        headers={"Cookie": cookie, **extra},
    )
    return answer


def resize(cols: int, rows: int) -> str:
    return json.dumps({"cols": cols, "rows": rows})


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


def recorded(session: Session, deployment: Deployment) -> list[tuple[str, str]]:
    """The events of the deployment's one recording, without their times."""
    [recording] = Recording._load(session, deployment_id=deployment.id)
    stored = File(recording.path)
    stored.get()
    events = [json.loads(line) for line in stored.data.decode().splitlines()[1:]]
    assert [at for at, _, _ in events] == sorted(at for at, _, _ in events)
    return [(code, text) for _, code, text in events]


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
        assert attached(viewer) == 0
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
        assert attached(viewer) == 0
        assert viewer.receive_bytes() == b"alphabet"


def test_a_viewer_with_an_offset_inside_the_tail_resumes_there(
    shipper: Shipper, operator: Any, deployment: Deployment
) -> None:
    with shipper.connect(deployment.id) as agent:
        offset(agent)
        ship(agent, 0, b"alphabet")

    with watch(operator, deployment.id, "?offset=5") as viewer:
        assert attached(viewer) == 5
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
        attached(viewer)
        offset(agent)
        ship(agent, 0, b"alphabet")

        assert viewer.receive_bytes() == b"alphabet"


@small(console_viewer_buffer_bytes=2, console_tail_bytes=4)
def test_a_viewer_past_its_cap_closes_with_4429_once_the_tail_lost_its_offset(
    shipper: Shipper, operator: Any, deployment: Deployment
) -> None:
    with watch(operator, deployment.id) as viewer, shipper.connect(deployment.id) as agent:
        attached(viewer)
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


@pytest.mark.parametrize(
    ("query", "start", "seen"),
    [("", 5, b"bet"), ("?offset=2", 5, b"bet"), ("?offset=6", 6, b"et")],
)
def test_a_trainee_link_starts_at_the_ready_offset(
    api: TestClient,
    session: Session,
    shipper: Shipper,
    deploy: Callable[..., Deployment],
    query: str,
    start: int,
    seen: bytes,
) -> None:
    ready = deploy(status=S.READY)
    _, cookie = opened(api, session, ready, Role.TRAINEE)
    with shipper.connect(ready.id) as agent:
        offset(agent)
        ship(agent, 0, b"alpha")
        ready.console.mark_ready(None)
        ship(agent, 5, b"bet")

        with join(api, ready.id, cookie, query, Origin="http://testserver") as viewer:
            assert attached(viewer) == start
            assert viewer.receive_bytes() == seen
            ship(agent, 8, b"ical")
            assert viewer.receive_bytes() == b"ical"


def test_a_viewer_link_without_a_ready_offset_starts_at_the_log_end(
    api: TestClient, session: Session, shipper: Shipper, deploy: Callable[..., Deployment]
) -> None:
    ready = deploy(status=S.READY)
    _, cookie = opened(api, session, ready, Role.VIEWER)
    with shipper.connect(ready.id) as agent:
        offset(agent)
        ship(agent, 0, b"alpha")

        with join(api, ready.id, cookie, "?offset=0") as viewer:
            assert attached(viewer) == 5
            ship(agent, 5, b"bet")
            assert viewer.receive_bytes() == b"bet"


def test_an_operator_link_gets_the_tail_from_its_start(
    api: TestClient, session: Session, shipper: Shipper, deploy: Callable[..., Deployment]
) -> None:
    ready = deploy(status=S.READY)
    _, cookie = opened(api, session, ready, Role.OPERATOR)
    with shipper.connect(ready.id) as agent:
        offset(agent)
        ship(agent, 0, b"alpha")
        ready.console.mark_ready(None)
        ship(agent, 5, b"bet")

    with join(api, ready.id, cookie) as viewer:
        assert attached(viewer) == 0
        assert viewer.receive_bytes() == b"alphabet"


def test_revoking_a_link_closes_its_socket_with_4403_and_no_other(
    api: TestClient,
    session: Session,
    operator: Any,
    shipper: Shipper,
    deploy: Callable[..., Deployment],
) -> None:
    ready = deploy(status=S.READY)
    link, cookie = opened(api, session, ready, Role.TRAINEE)
    _, other = opened(api, session, ready, Role.VIEWER, name="gamma")
    with (
        shipper.connect(ready.id) as agent,
        join(api, ready.id, cookie) as revoked,
        join(api, ready.id, other) as kept,
    ):
        offset(agent)
        assert (attached(revoked), attached(kept)) == (0, 0)

        assert operator.request("POST", f"/api/v1/links/{link.id}/revoke").is_success

        assert closed(revoked) == 4403
        ship(agent, 0, b"alpha")
        assert kept.receive_bytes() == b"alpha"
    assert refused(join(api, ready.id, cookie)) == 1008


@pytest.mark.parametrize("status", [S.PENDING, S.STARTING, S.BOOTSTRAPPING, S.STOPPING])
def test_a_link_is_refused_before_and_after_ready(
    api: TestClient, session: Session, deploy: Callable[..., Deployment], status: S
) -> None:
    waiting = deploy(status=status)
    _, cookie = opened(api, session, waiting, Role.TRAINEE)

    assert refused(join(api, waiting.id, cookie)) == 1008


def test_a_link_is_refused_on_another_deployment(
    api: TestClient, session: Session, deploy: Callable[..., Deployment]
) -> None:
    ready, other = deploy(status=S.READY), deploy(status=S.READY)
    _, cookie = opened(api, session, ready, Role.OPERATOR)

    assert refused(join(api, other.id, cookie)) == 1008


def test_a_revoked_link_or_a_disabled_user_is_refused(
    api: TestClient, session: Session, deploy: Callable[..., Deployment]
) -> None:
    ready = deploy(status=S.READY)
    revoked, cookie = opened(api, session, ready, Role.TRAINEE)
    link, other = opened(api, session, ready, Role.VIEWER, name="gamma")
    revoked.revoke()
    owner = User(session=session).load(id=link.user_id)
    owner.state = State.DISABLED
    owner.save()

    assert refused(join(api, ready.id, cookie)) == 1008
    assert refused(join(api, ready.id, other)) == 1008


@pytest.mark.parametrize("origin", ["https://example.com", "http://testserver.example.com", "null"])
def test_a_link_with_a_foreign_origin_is_refused(
    api: TestClient, session: Session, deploy: Callable[..., Deployment], origin: str
) -> None:
    ready = deploy(status=S.READY)
    _, cookie = opened(api, session, ready, Role.TRAINEE)

    assert refused(join(api, ready.id, cookie, Origin=origin)) == 1008


def test_a_missing_or_unknown_session_is_refused(
    api: TestClient, deploy: Callable[..., Deployment]
) -> None:
    ready = deploy(status=S.READY)

    assert refused(api.websocket_connect(f"{DEPLOYMENTS}/{ready.id}/attach")) == 1008
    assert refused(join(api, ready.id, "jatszoter_link=")) == 1008
    assert refused(join(api, ready.id, "jatszoter_link=alpha")) == 1008


def test_a_link_socket_closes_with_1000_once_the_deployment_is_terminal(
    api: TestClient,
    session: Session,
    deploy: Callable[..., Deployment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jatszoter_api.routes.console.POLL_SECONDS", 0.05)
    ready = deploy(status=S.READY)
    _, cookie = opened(api, session, ready, Role.TRAINEE)

    with join(api, ready.id, cookie) as viewer:
        attached(viewer)
        ready.transition("abort", S.READY, "lease expired", actor=Actor.API)

        assert closed(viewer) == 1000
    assert refused(join(api, ready.id, cookie)) == 1008


def test_a_trainee_links_keys_reach_the_agent_as_one_binary_frame(
    api: TestClient, session: Session, shipper: Shipper, deploy: Callable[..., Deployment]
) -> None:
    ready = deploy(status=S.READY)
    _, cookie = opened(api, session, ready, Role.TRAINEE)
    with shipper.connect(ready.id) as agent, join(api, ready.id, cookie) as trainee:
        offset(agent)
        attached(trainee)
        trainee.send_bytes(b"x" * 4096)

        assert agent.receive_bytes() == b"x" * 4096
        assert ship(agent, 0, b"alpha") == 5


def test_taking_control_moves_the_keyboard_and_giving_it_back_returns_it(
    api: TestClient,
    session: Session,
    operator: Any,
    shipper: Shipper,
    deploy: Callable[..., Deployment],
) -> None:
    ready = deploy(status=S.READY)
    _, cookie = opened(api, session, ready, Role.TRAINEE)
    with (
        shipper.connect(ready.id) as agent,
        join(api, ready.id, cookie) as trainee,
        watch(operator, ready.id) as signed,
    ):
        offset(agent)
        attached(trainee)
        attached(signed)

        taken = hand(operator, ready.id, "operator")
        assert (taken.status_code, taken.json()) == (200, {"holder": "operator"})
        assert (control(trainee), control(signed)) == ("operator", "operator")
        trainee.send_bytes(b"alpha")
        signed.send_bytes(b"beta")
        assert agent.receive_bytes() == b"beta"

        assert hand(operator, ready.id, "trainee").json() == {"holder": "trainee"}
        assert (control(trainee), control(signed)) == ("trainee", "trainee")
        signed.send_bytes(b"gamma")
        trainee.send_bytes(b"delta")
        assert agent.receive_bytes() == b"delta"
        assert ship(agent, 0, b"alpha") == 5


def test_an_operator_link_sets_the_holder_and_types_once_it_holds(
    api: TestClient, session: Session, shipper: Shipper, deploy: Callable[..., Deployment]
) -> None:
    ready = deploy(status=S.READY)
    _, cookie = opened(api, session, ready, Role.OPERATOR)
    with shipper.connect(ready.id) as agent, join(api, ready.id, cookie) as linked:
        offset(agent)
        attached(linked)

        taken = ask(api, ready.id, cookie, Origin="http://testserver")
        assert (taken.status_code, taken.json()) == (200, {"holder": "operator"})
        assert control(linked) == "operator"
        assert ready.console.control() is Holder.OPERATOR
        linked.send_bytes(b"beta")
        assert agent.receive_bytes() == b"beta"

        assert ask(api, ready.id, cookie, "trainee").status_code == 200
        assert control(linked) == "trainee"


def test_a_viewer_hears_the_holder_on_attach_and_on_a_change(
    api: TestClient, session: Session, operator: Any, deploy: Callable[..., Deployment]
) -> None:
    ready = deploy(status=S.READY)
    _, cookie = opened(api, session, ready, Role.VIEWER)
    with join(api, ready.id, cookie) as first:
        offset(first)
        assert control(first) == "trainee"
        hand(operator, ready.id, "operator")
        assert control(first) == "operator"

        with watch(operator, ready.id) as second:
            offset(second)
            assert control(second) == "operator"
            hand(operator, ready.id, "trainee")
            assert (control(first), control(second)) == ("trainee", "trainee")


def test_the_holders_resize_reaches_the_agent_as_text_and_no_other_partys(
    api: TestClient,
    session: Session,
    operator: Any,
    shipper: Shipper,
    deploy: Callable[..., Deployment],
) -> None:
    ready = deploy(status=S.READY)
    _, cookie = opened(api, session, ready, Role.TRAINEE)
    with (
        shipper.connect(ready.id) as agent,
        join(api, ready.id, cookie) as trainee,
        watch(operator, ready.id) as signed,
    ):
        offset(agent)
        attached(trainee)
        attached(signed)
        signed.send_text(resize(200, 50))
        trainee.send_text(resize(120, 40))
        assert json.loads(agent.receive_text()) == {"cols": 120, "rows": 40}

        hand(operator, ready.id, "operator")
        control(trainee)
        control(signed)
        trainee.send_text(resize(80, 24))
        signed.send_text(resize(500, 1))
        assert json.loads(agent.receive_text()) == {"cols": 500, "rows": 1}
        assert ship(agent, 0, b"alpha") == 5


def test_a_viewer_links_keys_and_resizes_never_arrive(
    api: TestClient, session: Session, shipper: Shipper, deploy: Callable[..., Deployment]
) -> None:
    ready = deploy(status=S.READY)
    _, cookie = opened(api, session, ready, Role.TRAINEE)
    _, other = opened(api, session, ready, Role.VIEWER, name="gamma")
    with (
        shipper.connect(ready.id) as agent,
        join(api, ready.id, cookie) as trainee,
        join(api, ready.id, other) as viewer,
    ):
        offset(agent)
        attached(trainee)
        attached(viewer)
        viewer.send_bytes(b"alpha")
        viewer.send_text(resize(80, 24))
        trainee.send_bytes(b"beta")

        assert agent.receive_bytes() == b"beta"
        assert ship(agent, 0, b"alpha") == 5


@pytest.mark.parametrize(
    "sent",
    [
        '{"cols": 0, "rows": 24}',
        '{"cols": 80, "rows": 501}',
        '{"cols": 80}',
        '{"cols": true, "rows": 24}',
        '{"cols": 80.5, "rows": 24}',
        '{"cols": "80", "rows": "24"}',
        "[80, 24]",
        "alpha",
    ],
)
def test_a_text_frame_that_is_no_resize_is_dropped(
    api: TestClient,
    session: Session,
    shipper: Shipper,
    deploy: Callable[..., Deployment],
    sent: str,
) -> None:
    ready = deploy(status=S.READY)
    _, cookie = opened(api, session, ready, Role.TRAINEE)
    with shipper.connect(ready.id) as agent, join(api, ready.id, cookie) as trainee:
        offset(agent)
        attached(trainee)
        trainee.send_text(sent)
        trainee.send_text('{"cols": 1, "rows": 500, "alpha": 1}')

        assert json.loads(agent.receive_text()) == {"cols": 1, "rows": 500}


def test_a_key_frame_over_4096_bytes_closes_with_1009(
    api: TestClient, session: Session, shipper: Shipper, deploy: Callable[..., Deployment]
) -> None:
    ready = deploy(status=S.READY)
    _, cookie = opened(api, session, ready, Role.TRAINEE)
    with shipper.connect(ready.id) as agent, join(api, ready.id, cookie) as trainee:
        offset(agent)
        attached(trainee)
        trainee.send_bytes(b"x" * 4097)

        assert closed(trainee) == 1009
        assert ship(agent, 0, b"alpha") == 5


def test_keys_with_no_agent_attached_keep_the_viewer_open(
    api: TestClient, session: Session, shipper: Shipper, deploy: Callable[..., Deployment]
) -> None:
    ready = deploy(status=S.READY)
    _, cookie = opened(api, session, ready, Role.TRAINEE)
    with join(api, ready.id, cookie) as trainee:
        attached(trainee)
        trainee.send_bytes(b"alpha")
        trainee.send_bytes(b"x" * 4097)

        # 1009 answers the second frame, so the first was read and left the socket open.
        assert closed(trainee) == 1009
    with shipper.connect(ready.id) as agent:
        assert offset(agent) == 0
        assert ship(agent, 0, b"beta") == 4


def test_the_agent_queue_takes_256_messages_and_drops_the_rest() -> None:
    async def fill() -> tuple[int, int, bool]:
        hub = ConsoleHub(8)
        target = uuid.uuid4()
        with hub.agent(target) as socket:
            taken = [hub.to_agent(target, b"a") for _ in range(257)]
            return taken.count(True), len(socket.take()), hub.to_agent(uuid.uuid4(), "{}")

    assert asyncio.run(fill()) == (256, 256, False)


@pytest.mark.parametrize("status", [S.PENDING, S.STARTING, S.BOOTSTRAPPING, S.STOPPING])
def test_control_is_409_unless_the_deployment_is_ready(
    operator: Any, deploy: Callable[..., Deployment], status: S
) -> None:
    waiting = deploy(status=status)

    assert hand(operator, waiting.id, "operator").status_code == 409
    assert waiting.console.control() is Holder.TRAINEE


@pytest.mark.parametrize("role", [Role.TRAINEE, Role.VIEWER])
def test_control_is_403_for_a_trainee_or_viewer_link(
    api: TestClient, session: Session, deploy: Callable[..., Deployment], role: Role
) -> None:
    ready = deploy(status=S.READY)
    _, cookie = opened(api, session, ready, role)

    assert ask(api, ready.id, cookie).status_code == 403
    assert ready.console.control() is Holder.TRAINEE


@pytest.mark.parametrize("origin", ["https://example.com", "http://testserver.example.com", "null"])
def test_control_with_a_foreign_origin_is_refused(
    api: TestClient, session: Session, deploy: Callable[..., Deployment], origin: str
) -> None:
    ready = deploy(status=S.READY)
    _, cookie = opened(api, session, ready, Role.OPERATOR)

    assert ask(api, ready.id, cookie, Origin=origin).status_code == 403
    assert ready.console.control() is Holder.TRAINEE


def test_control_is_refused_to_everyone_but_this_deployments_operator(
    api: TestClient,
    session: Session,
    sign_in: Callable[..., Any],
    operator: Any,
    deploy: Callable[..., Deployment],
) -> None:
    ready, other = deploy(status=S.READY), deploy(status=S.READY)
    link, cookie = opened(api, session, other, Role.OPERATOR)
    trainee = sign_in(name="gamma", role=Role.TRAINEE)
    path = f"{DEPLOYMENTS}/{ready.id}/control"

    assert api.post(path, json={"holder": "operator"}).status_code == 401
    assert ask(api, ready.id, "jatszoter_link=alpha").status_code == 401
    assert ask(api, ready.id, cookie).status_code == 403
    assert hand(trainee, ready.id, "operator").status_code == 403
    assert hand(operator, uuid.uuid4(), "operator").status_code == 404
    assert hand(operator, ready.id, "viewer").status_code == 422
    link.revoke()
    assert ask(api, other.id, cookie).status_code == 401
    assert ready.console.control() is Holder.TRAINEE
    assert other.console.control() is Holder.TRAINEE


def test_a_trainee_links_session_is_recorded_in_order(
    api: TestClient,
    session: Session,
    operator: Any,
    shipper: Shipper,
    deploy: Callable[..., Deployment],
) -> None:
    ready = deploy(status=S.READY)
    with shipper.connect(ready.id) as agent:
        offset(agent)
        ship(agent, 0, b"boot")
        ready.console.mark_ready(None)
        _, cookie = opened(api, session, ready, Role.TRAINEE)
        with join(api, ready.id, cookie) as trainee:
            attached(trainee)
            ship(agent, 4, b"alpha")
            trainee.send_text(resize(100, 30))
            assert json.loads(agent.receive_text()) == {"cols": 100, "rows": 30}
            # The next frame is read only once the resize is recorded.
            trainee.send_bytes(b"x")
            assert agent.receive_bytes() == b"x"
            hand(operator, ready.id, "operator")
            hand(operator, ready.id, "operator")
            ship(agent, 9, b"beta")
            hand(operator, ready.id, "trainee")

    assert recorded(session, ready) == [
        ("o", "alpha"),
        ("r", "100x30"),
        ("m", "control operator"),
        ("o", "beta"),
        ("m", "control trainee"),
    ]


def test_keys_never_reach_the_recording(
    api: TestClient,
    session: Session,
    operator: Any,
    shipper: Shipper,
    deploy: Callable[..., Deployment],
) -> None:
    ready = deploy(status=S.READY)
    ready.console.mark_ready(None)
    _, cookie = opened(api, session, ready, Role.TRAINEE)
    with (
        shipper.connect(ready.id) as agent,
        join(api, ready.id, cookie) as trainee,
        watch(operator, ready.id) as signed,
    ):
        offset(agent)
        attached(trainee)
        attached(signed)
        trainee.send_bytes(b"gamma")
        assert agent.receive_bytes() == b"gamma"
        hand(operator, ready.id, "operator")
        signed.send_bytes(b"delta")
        assert agent.receive_bytes() == b"delta"
        ship(agent, 0, b"alpha")

    assert recorded(session, ready) == [("m", "control operator"), ("o", "alpha")]


def test_a_resize_the_agent_did_not_take_or_from_another_party_is_not_recorded(
    api: TestClient,
    session: Session,
    operator: Any,
    deploy: Callable[..., Deployment],
) -> None:
    ready = deploy(status=S.READY)
    ready.console.mark_ready(None)
    _, cookie = opened(api, session, ready, Role.TRAINEE)
    with join(api, ready.id, cookie) as trainee, watch(operator, ready.id) as signed:
        attached(trainee)
        attached(signed)
        signed.send_text(resize(200, 50))
        signed.send_bytes(b"x" * 4097)
        assert closed(signed) == 1009
        trainee.send_text(resize(100, 30))
        trainee.send_bytes(b"x" * 4097)
        # 1009 answers the second frame, so the resize before it was read.
        assert closed(trainee) == 1009

    assert recorded(session, ready) == []


def test_a_recording_that_cannot_be_written_leaves_the_console_working(
    api: TestClient,
    session: Session,
    operator: Any,
    shipper: Shipper,
    deploy: Callable[..., Deployment],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = deploy(status=S.READY)
    ready.console.mark_ready(None)
    _, cookie = opened(api, session, ready, Role.TRAINEE)
    append = File.append

    def refuse(self: File) -> None:
        if "/recordings/" in self.url:
            raise httpx.ConnectError("refused")
        append(self)

    monkeypatch.setattr(File, "append", refuse)
    with shipper.connect(ready.id) as agent, join(api, ready.id, cookie) as trainee:
        offset(agent)
        attached(trainee)

        assert ship(agent, 0, b"alpha") == 5
        assert trainee.receive_bytes() == b"alpha"
        trainee.send_text(resize(100, 30))
        assert json.loads(agent.receive_text()) == {"cols": 100, "rows": 30}
        assert hand(operator, ready.id, "operator").status_code == 200
    assert log(ready) == b"alpha"
    assert recorded(session, ready) == []


def test_a_trainee_attaching_after_the_cache_lost_the_recording_resumes_it(
    api: TestClient, session: Session, shipper: Shipper, deploy: Callable[..., Deployment]
) -> None:
    ready = deploy(status=S.READY)
    ready.console.mark_ready(None)
    _, cookie = opened(api, session, ready, Role.TRAINEE)
    with shipper.connect(ready.id) as agent:
        offset(agent)
        ship(agent, 0, b"alpha")
        [recording] = Recording._load(session, deployment_id=ready.id)
        names = ("offset", "size", "rest", "full")
        Cached._client().delete(
            f"recording:of:{ready.id}", *(f"recording:{recording.id}:{name}" for name in names)
        )
        ship(agent, 5, b"beta")

        with join(api, ready.id, cookie) as trainee:
            attached(trainee)
            ship(agent, 9, b"gamma")

    assert recorded(session, ready) == [("o", "alpha"), ("o", "gamma")]
