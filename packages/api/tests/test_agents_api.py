import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session

from jatszoter_api.app import create_app
from jatszoter_api.file import File
from jatszoter_api.models import Agent, Deployment, Image, Scenario, State
from jatszoter_api.models._cache import Cached
from jatszoter_api.models.agent import LEASE_SECONDS
from jatszoter_api.settings import Settings

SIZING: dict[str, int] = {
    "cpus": 2,
    "memory_mib": 2048,
    "disk_gib": 8,
    "bootstrap_seconds": 600,
    "check_seconds": 30,
    "session_minutes": 45,
}


@pytest.fixture
def client(session: Session, settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        yield client


class Enrolled:
    def __init__(self, client: TestClient, claimed: dict[str, Any]) -> None:
        self.client = client
        self.id = claimed["agent_id"]
        self.credential = claimed["credential"]["credential"]
        self.key = claimed["credential"]["key"]

    def call(self, method: str, suffix: str, payload: Any = None, **headers: str) -> Response:
        path = f"/api/v1/agents/{self.id}{suffix}"
        body = b"" if payload is None else json.dumps(payload).encode()
        timestamp = str(int(time.time()))
        message = "\n".join((timestamp, method, path, hashlib.sha256(body).hexdigest()))
        signed = {
            "Content-Type": "application/json",
            "X-Credential": self.credential,
            "X-Timestamp": timestamp,
            "X-Signature": hmac.new(
                self.key.encode(), message.encode(), hashlib.sha256
            ).hexdigest(),
        }
        signed.update(headers)
        response: Response = self.client.request(method, path, content=body, headers=signed)
        return response


def register(client: TestClient, name: str = "alpha") -> str:
    response = client.post("/api/v1/agents/register", json={"name": name, "labels": ["beta"]})
    assert response.status_code == 201
    code: str = response.json()["claim_code"]
    return code


def claim(client: TestClient, code: str) -> Response:
    response: Response = client.post("/api/v1/agents/claim", json={"code": code})
    return response


def approve(session: Session, code: str) -> None:
    agent_id = Cached()._get(f"claim:{hashlib.sha256(code.encode()).hexdigest()}")
    assert agent_id is not None
    try:
        agent = Agent(session=session).load(id=agent_id)
    except IndexError:
        agent = Agent(session=session, id=agent_id)
    assert agent.approve() is not None


def enroll(client: TestClient, session: Session, name: str = "alpha") -> Enrolled:
    code = register(client, name)
    approve(session, code)
    response = claim(client, code)
    assert response.status_code == 200
    return Enrolled(client, response.json())


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "alpha", "extra": 1}])
def test_a_malformed_registration_is_refused(client: TestClient, body: dict[str, Any]) -> None:
    assert client.post("/api/v1/agents/register", json=body).status_code == 422


def test_a_registered_agent_waits_for_the_operator(client: TestClient) -> None:
    code = register(client)

    assert claim(client, code).status_code == 409


def test_an_approved_agent_claims_once_in_a_signed_response(
    client: TestClient, session: Session
) -> None:
    code = register(client)
    approve(session, code)

    first = claim(client, code)
    second = claim(client, code)

    assert first.status_code == 200
    expected = hmac.new(code.encode(), first.content, hashlib.sha256).hexdigest()
    assert first.headers["X-Signature"] == expected
    assert second.status_code == 401


def test_an_unknown_code_is_refused(client: TestClient) -> None:
    assert claim(client, "gamma").status_code == 401


def test_a_signed_heartbeat_opens_a_lease(client: TestClient, session: Session) -> None:
    agent = enroll(client, session)

    response = agent.call("POST", "/heartbeat", {"capacity": 2})

    assert response.status_code == 200
    lease = response.json()
    assert lease["capacity"] == 2
    assert lease["expires_at"] == lease["heartbeat_at"] + LEASE_SECONDS


def test_a_heartbeat_keeps_its_lease(client: TestClient, session: Session) -> None:
    agent = enroll(client, session)
    first = agent.call("POST", "/heartbeat", {"capacity": 1}).json()

    second = agent.call("POST", "/heartbeat", {"capacity": 1}).json()

    assert second["id"] == first["id"]


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Signature": "0" * 64},
        {"X-Timestamp": "1"},
        {"X-Timestamp": "later"},
        {"X-Credential": "current:1"},
        {"X-Credential": ""},
    ],
)
def test_an_unsigned_or_altered_heartbeat_is_refused(
    client: TestClient, session: Session, headers: dict[str, str]
) -> None:
    agent = enroll(client, session)

    assert agent.call("POST", "/heartbeat", {"capacity": 1}, **headers).status_code == 401


def test_a_credential_signs_for_its_own_agent_only(client: TestClient, session: Session) -> None:
    alpha, beta = enroll(client, session), enroll(client, session, "beta")
    alpha.id = beta.id

    assert alpha.call("POST", "/heartbeat", {"capacity": 1}).status_code == 401


def test_an_unknown_agent_is_refused(client: TestClient, session: Session) -> None:
    agent = enroll(client, session)
    agent.id = str(uuid.uuid4())

    assert agent.call("POST", "/heartbeat", {"capacity": 1}).status_code == 401


def test_a_disabled_agent_is_refused(client: TestClient, session: Session) -> None:
    agent = enroll(client, session)
    stored = Agent(session=session).load(id=uuid.UUID(agent.id))
    stored.state = State.DISABLED
    stored.save()

    assert agent.call("POST", "/heartbeat", {"capacity": 1}).status_code == 401


def test_a_refreshed_credential_signs(client: TestClient, session: Session) -> None:
    agent = enroll(client, session)

    response = agent.call("POST", "/credential")
    fresh = Enrolled(client, {"agent_id": agent.id, "credential": response.json()})

    assert response.status_code == 200
    assert fresh.credential != agent.credential
    assert fresh.call("POST", "/heartbeat", {"capacity": 1}).status_code == 200


def test_a_disabled_agent_registers_again_under_its_id(
    client: TestClient, session: Session
) -> None:
    agent = enroll(client, session)
    stored = Agent(session=session).load(id=uuid.UUID(agent.id))
    stored.state = State.DISABLED
    stored.save()

    response = client.post("/api/v1/agents/register", json={"name": "alpha", "agent_id": agent.id})
    code = response.json()["claim_code"]
    waiting = claim(client, code)
    approve(session, code)
    claimed = claim(client, code)

    assert waiting.status_code == 409
    assert claimed.status_code == 200
    assert claimed.json()["agent_id"] == agent.id
    again = Enrolled(client, claimed.json())
    assert again.call("POST", "/heartbeat", {"capacity": 1}).status_code == 200


@pytest.mark.parametrize("capacity", [-1, 65, "2"])
def test_a_heartbeat_needs_a_valid_capacity(
    client: TestClient, session: Session, capacity: Any
) -> None:
    agent = enroll(client, session)

    assert agent.call("POST", "/heartbeat", {"capacity": capacity}).status_code == 422


def test_the_app_enables_agents_at_once_when_its_settings_say_so(
    session: Session, settings: Settings
) -> None:
    auto = settings.model_copy(update={"auto_register_agents": True})

    with TestClient(create_app(auto)) as client:
        code = register(client)

        assert claim(client, code).status_code == 200


@pytest.fixture
def scenario(session: Session) -> Scenario:
    Image(session=session, name="debian-12", url="https://example.com/a", digest="a" * 64).save()
    scenario = Scenario(
        session=session,
        slug="nginx-502",
        version=3,
        document={"image": "debian-12"},
        digest="b" * 64,
    )
    scenario.save(File(scenario.bundle_path, b"bundle"))
    return scenario


def deploy(session: Session, scenario: Scenario, agent_id: str | None = None) -> Deployment:
    pinned = {} if agent_id is None else {"agent_id": uuid.UUID(agent_id)}
    deployment = Deployment(
        session=session,
        scenario_id=scenario.id,
        image_id=scenario.image.id,
        **SIZING,
        **pinned,
    )
    deployment.save()
    return deployment


def held(client: TestClient, session: Session, scenario: Scenario) -> tuple[Enrolled, str]:
    """An agent with a live lease and the one deployment its heartbeat assigned."""
    agent = enroll(client, session)
    deployment_id = str(deploy(session, scenario).id)
    assert agent.call("POST", "/heartbeat", {"capacity": 1}).status_code == 200
    return agent, deployment_id


def move(agent: Enrolled, deployment_id: str, **body: Any) -> Response:
    return agent.call("POST", f"/deployments/{deployment_id}/transition", body)


def test_the_desired_state_carries_the_image_of_the_deployment(
    client: TestClient, session: Session, scenario: Scenario
) -> None:
    agent = enroll(client, session)
    image = Image(session=session, name="debian-13", url="https://example.com/b", digest="c" * 64)
    image.save()
    deployment = deploy(session, scenario)
    deployment.image_id = image.id
    deployment.save()
    agent.call("POST", "/heartbeat", {"capacity": 1})

    response = agent.call("GET", "/deployments")

    assert response.status_code == 200
    [entry] = response.json()
    session.refresh(deployment)
    assert entry == {
        "id": str(deployment.id),
        "vm_id": str(deployment.vm_id),
        "status": "PENDING",
        "scenario": {"id": str(scenario.id), "slug": "nginx-502", "version": 3},
        "image": {"name": "debian-13", "url": "https://example.com/b", "digest": "c" * 64},
        "resources": {"cpus": 2, "memory_mib": 2048, "disk_gib": 8},
        "timeouts": {"bootstrap_seconds": 600, "check_seconds": 30, "session_minutes": 45},
    }


def test_an_agent_assigns_then_fails_its_deployment(
    client: TestClient, session: Session, scenario: Scenario
) -> None:
    agent, deployment_id = held(client, session, scenario)

    assigned = move(agent, deployment_id, event="assign", expected="PENDING")
    repeated = move(agent, deployment_id, event="assign", expected="PENDING")
    failed = move(agent, deployment_id, event="fail", expected="STARTING", reason="no disk")

    assert assigned.json() == {"id": deployment_id, "status": "STARTING"}
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "STARTING"
    assert failed.status_code == 200
    assert failed.json()["status"] == "FAILED"
    stored = Deployment(session=session).load(id=uuid.UUID(deployment_id))
    session.refresh(stored)
    assert stored.reason == "no disk"
    assert agent.call("GET", "/deployments").json() == []


def test_without_a_live_lease_nothing_is_read_or_moved(
    client: TestClient, session: Session, scenario: Scenario
) -> None:
    agent, deployment_id = held(client, session, scenario)
    Cached._client().delete(f"lease:{agent.id}")

    assert agent.call("GET", "/deployments").status_code == 409
    assert move(agent, deployment_id, event="assign", expected="PENDING").status_code == 409


def test_an_agent_that_never_heartbeat_has_no_desired_state(
    client: TestClient, session: Session
) -> None:
    agent = enroll(client, session)

    assert agent.call("GET", "/deployments").status_code == 409


@pytest.mark.parametrize(("event", "expected"), [("assign", "STARTING"), ("boot", "STARTING")])
def test_a_transition_from_the_wrong_status_conflicts(
    client: TestClient, session: Session, scenario: Scenario, event: str, expected: str
) -> None:
    agent, deployment_id = held(client, session, scenario)

    assert move(agent, deployment_id, event=event, expected=expected).status_code == 409


def test_fail_without_a_reason_is_refused(
    client: TestClient, session: Session, scenario: Scenario
) -> None:
    agent, deployment_id = held(client, session, scenario)

    assert move(agent, deployment_id, event="fail", expected="PENDING").status_code == 422


@pytest.mark.parametrize("event", ["stop", "cancel", "abort", "expire"])
def test_an_event_agents_do_not_send_is_refused(
    client: TestClient, session: Session, scenario: Scenario, event: str
) -> None:
    agent, deployment_id = held(client, session, scenario)

    assert move(agent, deployment_id, event=event, expected="PENDING").status_code == 422


def test_an_unknown_or_foreign_deployment_is_not_found(
    client: TestClient, session: Session, scenario: Scenario
) -> None:
    agent, _ = held(client, session, scenario)
    beta = enroll(client, session, "beta")
    foreign = deploy(session, scenario, beta.id)

    for deployment_id in (str(foreign.id), str(uuid.uuid4())):
        response = move(agent, deployment_id, event="assign", expected="PENDING")
        assert response.status_code == 404
