import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session

from jatszoter_api import bundles
from jatszoter_api.app import create_app
from jatszoter_api.file import File
from jatszoter_api.models import Agent, Check, CheckStatus, Deployment, Image, Scenario, State
from jatszoter_api.models._cache import Cached
from jatszoter_api.models.agent import LEASE_SECONDS
from jatszoter_api.settings import Settings

LIMIT = 1024 * 1024
Pack = Callable[..., bytes]
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
        "check": None,
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


@pytest.fixture
def sample(session: Session, pack: Pack) -> Scenario:
    """The sample scenario registered with its bundle in storage."""
    Image(session=session, name="rocky-9", url="https://example.com/r", digest="d" * 64).save()
    data = pack()
    checked = bundles.read(data, LIMIT)
    scenario = Scenario(
        session=session,
        slug=checked.document["id"],
        version=checked.document["version"],
        document=checked.document,
        digest=checked.digest,
    )
    scenario.save(File(scenario.bundle_path, data))
    return scenario


def starting(client: TestClient, session: Session, scenario: Scenario) -> tuple[Enrolled, str]:
    agent, deployment_id = held(client, session, scenario)
    assert move(agent, deployment_id, event="assign", expected="PENDING").status_code == 200
    return agent, deployment_id


def fetch(agent: Enrolled, deployment_id: str, **headers: str) -> Response:
    return agent.call("GET", f"/deployments/{deployment_id}/seed", **headers)


def test_a_starting_deployment_gets_its_seed(
    client: TestClient, session: Session, sample: Scenario, pack: Pack
) -> None:
    agent, deployment_id = starting(client, session, sample)

    response = fetch(agent, deployment_id)

    assert response.status_code == 200
    seed = response.json()
    assert set(seed) == {"user_data", "meta_data"}
    deployment = Deployment(session=session).load(id=uuid.UUID(deployment_id))
    session.refresh(deployment)
    meta = yaml.safe_load(seed["meta_data"])
    assert meta == {"instance-id": str(deployment.vm_id), "local-hostname": "nginx-502"}
    assert seed["user_data"].startswith("#cloud-config\n")
    written = {
        entry["path"]: base64.b64decode(entry["content"])
        for entry in yaml.safe_load(seed["user_data"])["write_files"]
    }
    sample_files = bundles.read(pack(), LIMIT).files
    assert written["/var/lib/jatszoter/steps/break.sh"] == sample_files["break.sh"]


def test_the_seed_of_an_unknown_or_foreign_deployment_is_not_found(
    client: TestClient, session: Session, sample: Scenario
) -> None:
    agent, _ = starting(client, session, sample)
    beta = enroll(client, session, "beta")
    foreign = deploy(session, sample, beta.id)

    for deployment_id in (str(foreign.id), str(uuid.uuid4())):
        assert fetch(agent, deployment_id).status_code == 404


@pytest.mark.parametrize("events", [[], ["boot"]], ids=["PENDING", "BOOTSTRAPPING"])
def test_only_a_starting_deployment_has_a_seed(
    client: TestClient, session: Session, sample: Scenario, events: list[str]
) -> None:
    agent, deployment_id = held(client, session, sample)
    if events:
        move(agent, deployment_id, event="assign", expected="PENDING")
        assert move(agent, deployment_id, event="boot", expected="STARTING").status_code == 200

    assert fetch(agent, deployment_id).status_code == 409


def test_without_a_live_lease_there_is_no_seed(
    client: TestClient, session: Session, sample: Scenario
) -> None:
    agent, deployment_id = starting(client, session, sample)
    Cached._client().delete(f"lease:{agent.id}")

    assert fetch(agent, deployment_id).status_code == 409


def test_an_unsigned_seed_request_is_refused(
    client: TestClient, session: Session, sample: Scenario
) -> None:
    agent, deployment_id = starting(client, session, sample)

    assert fetch(agent, deployment_id, **{"X-Signature": "0" * 64}).status_code == 401


@pytest.mark.parametrize(
    ("stored", "message"),
    [
        (lambda pack: None, "does not read"),
        (lambda pack: b"junk", "does not read"),
        (lambda pack: pack(changes={"break.sh": b"exit 0\n"}), "changed"),
    ],
    ids=["missing", "unreadable", "changed"],
)
def test_a_stored_bundle_that_no_longer_holds_is_never_served(
    client: TestClient,
    session: Session,
    sample: Scenario,
    pack: Pack,
    stored: Callable[[Pack], bytes | None],
    message: str,
) -> None:
    agent, deployment_id = starting(client, session, sample)
    data = stored(pack)
    if data is None:
        File(sample.bundle_path).delete()
    else:
        File(sample.bundle_path, data).put()

    with pytest.raises(RuntimeError, match=message):
        fetch(agent, deployment_id)


PASSED = {"status": "passed", "exit_code": 0}


def ready(client: TestClient, session: Session, scenario: Scenario) -> tuple[Enrolled, str]:
    agent, deployment_id = starting(client, session, scenario)
    assert move(agent, deployment_id, event="boot", expected="STARTING").status_code == 200
    booted = move(agent, deployment_id, event="bootstrap", expected="BOOTSTRAPPING")
    assert booted.json()["status"] == "READY"
    return agent, deployment_id


def asked(session: Session, deployment_id: Any) -> Check:
    """A pending check of the deployment, stored the way a request stores it."""
    owner = uuid.UUID(str(deployment_id))
    check = Check(session=session, deployment_id=owner, user_id=uuid.uuid4())
    check.save()
    return check


def checked(agent: Enrolled, check: Check, payload: Any = None, **headers: str) -> Response:
    """The script of `check` without a payload, its result with one."""
    suffix = f"/deployments/{check.deployment_id}/checks/{check.id}"
    return agent.call("GET" if payload is None else "POST", suffix, payload, **headers)


def both(agent: Enrolled, check: Check) -> set[int]:
    return {checked(agent, check).status_code, checked(agent, check, PASSED).status_code}


def test_the_desired_state_names_the_pending_check_and_null_without_one(
    client: TestClient, session: Session, scenario: Scenario
) -> None:
    agent, deployment_id = ready(client, session, scenario)

    before = agent.call("GET", "/deployments").json()
    check = asked(session, deployment_id)
    pending = agent.call("GET", "/deployments").json()
    assert checked(agent, check, PASSED).status_code == 200
    after = agent.call("GET", "/deployments").json()

    assert [entry["check"] for entry in (*before, *pending, *after)] == [None, str(check.id), None]


def test_a_pending_check_gets_its_script_and_the_timeout(
    client: TestClient, session: Session, sample: Scenario, pack: Pack
) -> None:
    agent, deployment_id = ready(client, session, sample)

    response = checked(agent, asked(session, deployment_id))

    assert response.status_code == 200
    assert response.json() == {
        "script": bundles.read(pack(), LIMIT).files["check.sh"].decode(),
        "timeout_seconds": SIZING["check_seconds"],
    }


@pytest.mark.parametrize(
    ("status", "exit_code"), [("passed", 0), ("failed", 2), ("error", None), ("timeout", None)]
)
def test_each_status_is_stored_with_its_exit_code(
    client: TestClient, session: Session, scenario: Scenario, status: str, exit_code: int | None
) -> None:
    agent, deployment_id = ready(client, session, scenario)
    check = asked(session, deployment_id)
    payload = {"status": status, "exit_code": exit_code, "message": "alpha", "detail": "beta"}

    response = checked(agent, check, payload)

    assert response.status_code == 200
    assert response.json() == {"id": str(check.id), "status": status}
    session.refresh(check)
    assert (check.status, check.exit_code) == (CheckStatus(status), exit_code)
    assert (check.message, check.detail) == ("alpha", "beta")
    assert check.finished_at is not None


def test_a_long_message_and_a_long_detail_are_cut(
    client: TestClient, session: Session, scenario: Scenario
) -> None:
    agent, deployment_id = ready(client, session, scenario)
    check = asked(session, deployment_id)
    payload = {
        "status": "failed",
        "exit_code": 1,
        "message": "a" * 300 + "\nbeta",
        "detail": "\u00e9" * 4096,
    }

    assert checked(agent, check, payload).status_code == 200

    session.refresh(check)
    assert (check.message, check.detail) == ("a" * 200, "\u00e9" * 2048)


def test_a_result_without_a_message_or_a_detail_stores_none(
    client: TestClient, session: Session, scenario: Scenario
) -> None:
    agent, deployment_id = ready(client, session, scenario)
    check = asked(session, deployment_id)

    assert checked(agent, check, PASSED).status_code == 200

    session.refresh(check)
    assert (check.status, check.message, check.detail) == (CheckStatus.PASSED, None, None)


def test_a_check_of_an_unknown_or_foreign_deployment_is_not_found(
    client: TestClient, session: Session, scenario: Scenario
) -> None:
    agent, deployment_id = ready(client, session, scenario)
    own = asked(session, deployment_id)
    beta = enroll(client, session, "beta")
    foreign = asked(session, deploy(session, scenario, beta.id).id)
    nowhere = Check(session=session, deployment_id=uuid.uuid4(), user_id=uuid.uuid4())
    unknown = Check(session=session, deployment_id=own.deployment_id, user_id=uuid.uuid4())
    moved = Check(
        session=session, id=foreign.id, deployment_id=own.deployment_id, user_id=uuid.uuid4()
    )

    for check in (foreign, nowhere, unknown, moved):
        assert both(agent, check) == {404}
    session.refresh(foreign)
    assert foreign.status is CheckStatus.PENDING


def test_without_a_live_lease_a_check_is_neither_read_nor_settled(
    client: TestClient, session: Session, scenario: Scenario
) -> None:
    agent, deployment_id = ready(client, session, scenario)
    check = asked(session, deployment_id)
    Cached._client().delete(f"lease:{agent.id}")

    assert both(agent, check) == {409}
    session.refresh(check)
    assert check.status is CheckStatus.PENDING


def test_a_check_of_a_deployment_that_is_not_ready_is_refused(
    client: TestClient, session: Session, scenario: Scenario
) -> None:
    agent, deployment_id = starting(client, session, scenario)
    check = asked(session, deployment_id)

    assert both(agent, check) == {409}
    session.refresh(check)
    assert check.status is CheckStatus.PENDING


def test_a_settled_check_is_refused_and_a_second_result_changes_nothing(
    client: TestClient, session: Session, scenario: Scenario
) -> None:
    agent, deployment_id = ready(client, session, scenario)
    check = asked(session, deployment_id)
    failed = {"status": "failed", "exit_code": 1, "message": "alpha"}
    assert checked(agent, check, failed).status_code == 200
    session.refresh(check)
    finished_at = check.finished_at

    assert both(agent, check) == {409}

    session.refresh(check)
    assert (check.status, check.exit_code, check.message) == (CheckStatus.FAILED, 1, "alpha")
    assert check.finished_at == finished_at


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "passed", "exit_code": 1},
        {"status": "passed"},
        {"status": "failed", "exit_code": 0},
        {"status": "failed"},
        {"status": "timeout", "exit_code": 1},
        {"status": "error", "exit_code": 0},
        {"status": "pending"},
        {"exit_code": 0},
        {"status": "passed", "exit_code": "0"},
        {"status": "passed", "exit_code": 0, "extra": 1},
    ],
)
def test_an_inconsistent_result_is_refused(
    client: TestClient, session: Session, scenario: Scenario, payload: dict[str, Any]
) -> None:
    agent, deployment_id = ready(client, session, scenario)
    check = asked(session, deployment_id)

    assert checked(agent, check, payload).status_code == 422
    session.refresh(check)
    assert check.status is CheckStatus.PENDING


def test_an_unsigned_check_request_is_refused(
    client: TestClient, session: Session, scenario: Scenario
) -> None:
    agent, deployment_id = ready(client, session, scenario)
    check = asked(session, deployment_id)
    forged = {"X-Signature": "0" * 64}

    assert checked(agent, check, **forged).status_code == 401
    assert checked(agent, check, PASSED, **forged).status_code == 401
    session.refresh(check)
    assert check.status is CheckStatus.PENDING


@pytest.mark.parametrize(
    ("stored", "message"),
    [
        (lambda pack: None, "does not read"),
        (lambda pack: b"junk", "does not read"),
        (lambda pack: pack(changes={"check.sh": b"exit 0\n"}), "changed"),
    ],
    ids=["missing", "unreadable", "changed"],
)
def test_a_stored_bundle_that_no_longer_holds_serves_no_script_and_the_check_stays_pending(
    client: TestClient,
    session: Session,
    sample: Scenario,
    pack: Pack,
    stored: Callable[[Pack], bytes | None],
    message: str,
) -> None:
    agent, deployment_id = ready(client, session, sample)
    check = asked(session, deployment_id)
    data = stored(pack)
    if data is None:
        File(sample.bundle_path).delete()
    else:
        File(sample.bundle_path, data).put()

    with pytest.raises(RuntimeError, match=message):
        checked(agent, check)

    session.refresh(check)
    assert check.status is CheckStatus.PENDING
