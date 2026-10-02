import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlmodel import Session

from jatszoter_api.file import File
from jatszoter_api.lifecycle import DeploymentStatus
from jatszoter_api.models import Agent, Deployment, Image, Role, Scenario, State

S = DeploymentStatus
DEPLOYMENTS = "/api/v1/deployments"
DOCUMENT: dict[str, Any] = {
    "id": "nginx-502",
    "version": 1,
    "title": "Nginx answers 502",
    "image": "debian-12",
    "resources": {"cpus": 2, "memory_mib": 2048},
    "timeouts": {"session_minutes": 30},
}
SIZING: dict[str, int] = {
    "cpus": 1,
    "memory_mib": 512,
    "disk_gib": 4,
    "bootstrap_seconds": 600,
    "check_seconds": 30,
    "session_minutes": 60,
}


@pytest.fixture
def image(session: Session) -> Image:
    image = Image(session=session, name="debian-12", url="https://example.com/d", digest="a" * 64)
    image.save()
    return image


def register(session: Session, document: dict[str, Any]) -> Scenario:
    scenario = Scenario(
        session=session,
        slug=document["id"],
        version=document["version"],
        document=document,
        digest="b" * 64,
    )
    scenario.save(File(scenario.bundle_path, b"bundle"))
    return scenario


@pytest.fixture
def scenario(session: Session, image: Image) -> Scenario:
    return register(session, DOCUMENT)


def deploy(
    session: Session, scenario: Scenario, status: S = S.PENDING, created_at: int | None = None
) -> Deployment:
    known = {} if created_at is None else {"created_at": created_at}
    deployment = Deployment(
        session=session,
        scenario_id=scenario.id,
        image_id=scenario.image.id,
        status=status,
        **SIZING,
        **known,
    )
    deployment.save()
    return deployment


def agent(session: Session, state: State) -> Agent:
    agent = Agent(session=session, name="alpha", state=state)
    agent.save()
    return agent


def test_create_copies_the_scenario_and_falls_back_to_settings(
    operator: Any, scenario: Scenario, image: Image
) -> None:
    response = operator.request("POST", DEPLOYMENTS, json={"scenario_id": str(scenario.id)})

    assert response.status_code == 201
    created = response.json()
    assert created["scenario_id"] == str(scenario.id)
    assert created["image_id"] == str(image.id)
    assert created["agent_id"] is None
    assert created["status"] == S.PENDING
    assert {name: created[name] for name in SIZING} == {
        "cpus": 2,
        "memory_mib": 2048,
        "disk_gib": 10,
        "bootstrap_seconds": 900,
        "check_seconds": 30,
        "session_minutes": 30,
    }
    assert operator.request("GET", f"{DEPLOYMENTS}/{created['id']}").json() == created


def test_a_pinned_agent_is_stored(operator: Any, session: Session, scenario: Scenario) -> None:
    pinned = agent(session, State.ENABLED)

    response = operator.request(
        "POST", DEPLOYMENTS, json={"scenario_id": str(scenario.id), "agent_id": str(pinned.id)}
    )

    assert response.status_code == 201
    assert response.json()["agent_id"] == str(pinned.id)


def test_an_unknown_scenario_is_not_found(operator: Any) -> None:
    response = operator.request("POST", DEPLOYMENTS, json={"scenario_id": str(uuid.uuid4())})

    assert response.status_code == 404


def test_an_unregistered_image_conflicts(operator: Any, session: Session) -> None:
    orphan = register(session, {**DOCUMENT, "image": "beta"})

    response = operator.request("POST", DEPLOYMENTS, json={"scenario_id": str(orphan.id)})

    assert response.status_code == 409


@pytest.mark.parametrize("enabled", [False, None])
def test_a_disabled_or_unknown_agent_is_unprocessable(
    operator: Any, session: Session, scenario: Scenario, enabled: bool | None
) -> None:
    agent_id = uuid.uuid4() if enabled is None else agent(session, State.DISABLED).id

    response = operator.request(
        "POST", DEPLOYMENTS, json={"scenario_id": str(scenario.id), "agent_id": str(agent_id)}
    )

    assert response.status_code == 422
    assert Deployment._load(session) == []


def test_the_list_filters_by_status_newest_first(
    operator: Any, session: Session, scenario: Scenario
) -> None:
    old = deploy(session, scenario, S.READY, created_at=100)
    new = deploy(session, scenario, S.READY, created_at=200)
    pending = deploy(session, scenario, created_at=300)

    ready = operator.request("GET", DEPLOYMENTS, params={"status": "READY"})
    every = operator.request("GET", DEPLOYMENTS)

    assert [row["id"] for row in ready.json()] == [str(new.id), str(old.id)]
    assert [row["id"] for row in every.json()] == [str(pending.id), str(new.id), str(old.id)]


def test_an_unknown_deployment_is_not_found(operator: Any) -> None:
    assert operator.request("GET", f"{DEPLOYMENTS}/{uuid.uuid4()}").status_code == 404
    assert operator.request("POST", f"{DEPLOYMENTS}/{uuid.uuid4()}/stop").status_code == 404


@pytest.mark.parametrize(
    ("status", "target"),
    [
        (S.PENDING, S.CANCELLED),
        (S.STARTING, S.STOPPING),
        (S.BOOTSTRAPPING, S.STOPPING),
        (S.READY, S.STOPPING),
        (S.STOPPING, S.STOPPING),
        (S.CANCELLED, S.CANCELLED),
    ],
)
def test_stop_lands_in_the_right_state(
    operator: Any, session: Session, scenario: Scenario, status: S, target: S
) -> None:
    deployment = deploy(session, scenario, status)

    response = operator.request("POST", f"{DEPLOYMENTS}/{deployment.id}/stop")

    assert response.status_code == 200
    assert response.json()["status"] == target


@pytest.mark.parametrize("status", [S.COMPLETED, S.FAILED])
def test_stopping_an_ended_deployment_conflicts(
    operator: Any, session: Session, scenario: Scenario, status: S
) -> None:
    deployment = deploy(session, scenario, status)

    response = operator.request("POST", f"{DEPLOYMENTS}/{deployment.id}/stop")

    assert response.status_code == 409


def test_only_an_operator_reaches_deployments(
    sign_in: Callable[..., Any], api: Any, session: Session, scenario: Scenario
) -> None:
    trainee = sign_in(name="beta", role=Role.TRAINEE)
    deployment = deploy(session, scenario)
    one = f"{DEPLOYMENTS}/{deployment.id}"
    body = {"scenario_id": str(scenario.id)}

    assert trainee.request("POST", DEPLOYMENTS, json=body).status_code == 403
    assert trainee.request("GET", DEPLOYMENTS).status_code == 403
    assert trainee.request("GET", one).status_code == 403
    assert trainee.request("POST", f"{one}/stop").status_code == 403
    assert api.get(DEPLOYMENTS).status_code == 401
