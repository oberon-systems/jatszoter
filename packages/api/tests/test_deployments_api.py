import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from jatszoter_api import bundles
from jatszoter_api.file import File
from jatszoter_api.lifecycle import DeploymentStatus
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

S = DeploymentStatus
DEPLOYMENTS = "/api/v1/deployments"
STORAGE = "/api/v1/storage"
LIMIT = 1024 * 1024
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


@pytest.fixture
def trainee(session: Session) -> User:
    user = User(session=session, name="gamma", role=Role.TRAINEE, state=State.ENABLED)
    user.save()
    return user


def body(scenario: Scenario, trainee: User, **more: str) -> dict[str, str]:
    return {"scenario_id": str(scenario.id), "trainee_id": str(trainee.id), **more}


def deploy(
    session: Session, scenario: Scenario, status: S = S.PENDING, created_at: int | None = None
) -> Deployment:
    known = {} if created_at is None else {"created_at": created_at}
    deployment = Deployment(
        session=session,
        scenario_id=scenario.id,
        image_id=scenario.image.id,
        trainee_id=uuid.uuid4(),
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
    operator: Any, scenario: Scenario, image: Image, trainee: User
) -> None:
    response = operator.request("POST", DEPLOYMENTS, json=body(scenario, trainee))

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
    del created["link"]
    assert operator.request("GET", f"{DEPLOYMENTS}/{created['id']}").json() == created


def test_a_pinned_agent_is_stored(
    operator: Any, session: Session, scenario: Scenario, trainee: User
) -> None:
    pinned = agent(session, State.ENABLED)

    response = operator.request(
        "POST", DEPLOYMENTS, json=body(scenario, trainee, agent_id=str(pinned.id))
    )

    assert response.status_code == 201
    assert response.json()["agent_id"] == str(pinned.id)


def test_an_unknown_scenario_is_not_found(operator: Any, trainee: User) -> None:
    unknown = {"scenario_id": str(uuid.uuid4()), "trainee_id": str(trainee.id)}

    response = operator.request("POST", DEPLOYMENTS, json=unknown)

    assert response.status_code == 404


def test_an_unregistered_image_conflicts(operator: Any, session: Session, trainee: User) -> None:
    orphan = register(session, {**DOCUMENT, "image": "beta"})

    response = operator.request("POST", DEPLOYMENTS, json=body(orphan, trainee))

    assert response.status_code == 409


@pytest.mark.parametrize("enabled", [False, None])
def test_a_disabled_or_unknown_agent_is_unprocessable(
    operator: Any, session: Session, scenario: Scenario, trainee: User, enabled: bool | None
) -> None:
    agent_id = uuid.uuid4() if enabled is None else agent(session, State.DISABLED).id

    response = operator.request(
        "POST", DEPLOYMENTS, json=body(scenario, trainee, agent_id=str(agent_id))
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
    sign_in: Callable[..., Any], api: Any, session: Session, scenario: Scenario, trainee: User
) -> None:
    signed = sign_in(name="beta", role=Role.TRAINEE)
    deployment = deploy(session, scenario)
    one = f"{DEPLOYMENTS}/{deployment.id}"

    assert signed.request("POST", DEPLOYMENTS, json=body(scenario, trainee)).status_code == 403
    assert signed.request("GET", DEPLOYMENTS).status_code == 403
    assert signed.request("GET", one).status_code == 403
    assert signed.request("POST", f"{one}/stop").status_code == 403
    assert signed.request("GET", f"{one}/links").status_code == 403
    assert signed.request("POST", f"{one}/links", json={"user_id": signed.id}).status_code == 403
    assert signed.request("POST", f"/api/v1/links/{uuid.uuid4()}/revoke").status_code == 403
    assert api.get(DEPLOYMENTS).status_code == 401


@pytest.fixture
def bundled(session: Session, pack: Callable[..., bytes]) -> tuple[Scenario, str]:
    """The sample scenario with its real bundle stored, and its task text."""
    data = pack()
    bundle = bundles.read(data, LIMIT)
    document = bundle.document
    image = Image(
        session=session, name=document["image"], url="https://example.com/r", digest="c" * 64
    )
    image.save()
    scenario = Scenario(
        session=session,
        slug=document["id"],
        version=document["version"],
        document=document,
        digest=bundle.digest,
    )
    scenario.save(File(scenario.bundle_path, data))
    return scenario, bundle.files[document["description"]].decode()


def link_cookie(api: TestClient, session: Session, deployment: Deployment, role: Role) -> str:
    owner = User(session=session, name=f"{role}-beta", role=role, state=State.ENABLED)
    owner.save()
    link = Link(session=session, deployment_id=deployment.id, user_id=owner.id)
    link.save()
    response = api.get(f"/s/{link.token}", follow_redirects=False)
    assert response.status_code == 303
    cookie: str = response.headers["set-cookie"].partition(";")[0]
    return cookie


def by_cookie(api: TestClient, where: str, cookie: str) -> httpx.Response:
    response: httpx.Response = api.get(where, headers={"Cookie": cookie})
    return response


def test_a_deployment_answers_the_trainee_it_was_created_for(
    operator: Any, session: Session, scenario: Scenario, trainee: User
) -> None:
    created = operator.request("POST", DEPLOYMENTS, json=body(scenario, trainee)).json()

    shown = operator.request("GET", f"{DEPLOYMENTS}/{created['id']}").json()

    assert created["trainee_id"] == shown["trainee_id"] == str(trainee.id)
    assert Deployment(session=session).load(id=uuid.UUID(created["id"])).trainee_id == trainee.id


def test_the_task_answers_the_scenario_text_and_the_session_end(
    operator: Any, session: Session, bundled: tuple[Scenario, str]
) -> None:
    scenario, text = bundled
    deployment = deploy(session, scenario)
    where = f"{DEPLOYMENTS}/{deployment.id}/task"

    waiting = operator.request("GET", where)
    deployment.status, deployment.ready_at = S.READY, 1000
    deployment.save()
    ready = operator.request("GET", where)

    assert waiting.status_code == 200
    assert waiting.json() == {
        "status": "PENDING",
        "title": scenario.document["title"],
        "description": text,
        "role": "operator",
        "session_ends_at": None,
    }
    assert ready.json()["status"] == "READY"
    assert ready.json()["session_ends_at"] == 1000 + SIZING["session_minutes"] * 60


@pytest.mark.parametrize("role", [Role.TRAINEE, Role.VIEWER])
def test_a_link_reads_the_task_with_its_role(
    api: TestClient, session: Session, bundled: tuple[Scenario, str], role: Role
) -> None:
    scenario, text = bundled
    deployment = deploy(session, scenario, S.READY)
    cookie = link_cookie(api, session, deployment, role)

    response = by_cookie(api, f"{DEPLOYMENTS}/{deployment.id}/task", cookie)

    assert response.status_code == 200
    assert (response.json()["role"], response.json()["description"]) == (role, text)


def test_the_task_is_refused_to_everyone_but_a_party_of_the_deployment(
    sign_in: Callable[..., Any],
    operator: Any,
    api: TestClient,
    session: Session,
    bundled: tuple[Scenario, str],
) -> None:
    scenario, _ = bundled
    deployment, other = deploy(session, scenario), deploy(session, scenario)
    cookie = link_cookie(api, session, other, Role.TRAINEE)
    trainee = sign_in(name="gamma", role=Role.TRAINEE)
    where = f"{DEPLOYMENTS}/{deployment.id}/task"

    assert by_cookie(api, where, cookie).status_code == 403
    assert trainee.request("GET", where).status_code == 403
    assert api.get(where).status_code == 401
    assert operator.request("GET", f"{DEPLOYMENTS}/{uuid.uuid4()}/task").status_code == 404


def recorded(session: Session, deployment: Deployment, cast: bytes, size_bytes: int) -> Recording:
    recording = Recording(
        session=session, deployment_id=deployment.id, link_id=uuid.uuid4(), size_bytes=size_bytes
    )
    recording.save()
    File(recording.path, cast).put()
    return recording


def test_storage_counts_consoles_recordings_and_bundles(
    operator: Any, session: Session, scenario: Scenario
) -> None:
    ended = deploy(session, scenario, S.COMPLETED, created_at=100)
    live = deploy(session, scenario, S.READY, created_at=200)
    File(ended.console.path, b"console").put()
    recorded(session, ended, b"cast", 4)
    recorded(session, live, b"abc", 0)

    response = operator.request("GET", STORAGE)

    assert response.status_code == 200
    assert response.json() == {
        "deployments": [
            {
                "deployment_id": str(live.id),
                "console_bytes": 0,
                "recording_bytes": 3,
                "recordings": 1,
            },
            {
                "deployment_id": str(ended.id),
                "console_bytes": 7,
                "recording_bytes": 4,
                "recordings": 1,
            },
        ],
        "scenario_bytes": len(b"bundle"),
        "total_bytes": 3 + 7 + 4 + len(b"bundle"),
    }


@pytest.mark.parametrize("status", [S.COMPLETED, S.FAILED, S.CANCELLED])
def test_purge_deletes_the_console_and_the_recordings_and_keeps_the_deployment(
    operator: Any, session: Session, scenario: Scenario, status: S
) -> None:
    deployment = deploy(session, scenario, status)
    File(deployment.console.path, b"console").put()
    cast = recorded(session, deployment, b"cast", 4).path

    response = operator.request("POST", f"{DEPLOYMENTS}/{deployment.id}/purge")

    assert response.status_code == 200
    assert response.json()["status"] == status
    session.expire_all()
    assert Recording._load(session, deployment_id=deployment.id) == []
    assert File(cast).size() == File(deployment.console.path).size() == 0
    [held] = operator.request("GET", STORAGE).json()["deployments"]
    assert (held["console_bytes"], held["recording_bytes"], held["recordings"]) == (0, 0, 0)
    assert operator.request("POST", f"{DEPLOYMENTS}/{deployment.id}/purge").is_success


@pytest.mark.parametrize("status", [S.PENDING, S.STARTING, S.READY, S.STOPPING])
def test_purging_a_live_deployment_conflicts_and_deletes_nothing(
    operator: Any, session: Session, scenario: Scenario, status: S
) -> None:
    deployment = deploy(session, scenario, status)
    File(deployment.console.path, b"console").put()
    recording = recorded(session, deployment, b"cast", 0)

    response = operator.request("POST", f"{DEPLOYMENTS}/{deployment.id}/purge")

    assert response.status_code == 409
    assert File(deployment.console.path).size() == 7
    assert File(recording.path).size() == 4
    assert len(Recording._load(session, deployment_id=deployment.id)) == 1


def test_only_an_operator_reads_storage_or_purges(
    sign_in: Callable[..., Any],
    operator: Any,
    api: TestClient,
    session: Session,
    scenario: Scenario,
) -> None:
    trainee = sign_in(name="gamma", role=Role.TRAINEE)
    deployment = deploy(session, scenario, S.COMPLETED)
    purge = f"{DEPLOYMENTS}/{deployment.id}/purge"
    File(deployment.console.path, b"console").put()

    assert trainee.request("GET", STORAGE).status_code == 403
    assert trainee.request("POST", purge).status_code == 403
    assert api.get(STORAGE).status_code == 401
    assert operator.request("POST", f"{DEPLOYMENTS}/{uuid.uuid4()}/purge").status_code == 404
    assert File(deployment.console.path).size() == 7
