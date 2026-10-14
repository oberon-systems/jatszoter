import hashlib
import json
import logging
import secrets
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from jatszoter_api.errors import ConflictError
from jatszoter_api.file import File
from jatszoter_api.lifecycle import DeploymentStatus
from jatszoter_api.models import (
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
LINKS = "/api/v1/links"
LISTED = {"id", "user_id", "role", "created_at", "opened_at", "revoked_at"}


@pytest.fixture
def scenario(session: Session) -> Scenario:
    Image(session=session, name="debian-12", url="https://example.com/d", digest="a" * 64).save()
    scenario = Scenario(
        session=session,
        slug="nginx-502",
        version=1,
        document={"id": "nginx-502", "version": 1, "title": "alpha", "image": "debian-12"},
        digest="b" * 64,
    )
    scenario.save(File(scenario.bundle_path, b"bundle"))
    return scenario


def deploy(session: Session, scenario: Scenario, status: S) -> Deployment:
    deployment = Deployment(
        session=session,
        scenario_id=scenario.id,
        image_id=scenario.image.id,
        trainee_id=uuid.uuid4(),
        status=status,
        cpus=1,
        memory_mib=512,
        disk_gib=4,
        bootstrap_seconds=600,
        check_seconds=30,
        session_minutes=60,
    )
    deployment.save()
    return deployment


def user(session: Session, name: str, role: Role, state: State = State.ENABLED) -> User:
    stored = User(session=session, name=name, role=role, state=state)
    stored.save()
    return stored


@pytest.fixture
def trainee(session: Session) -> User:
    return user(session, "gamma", Role.TRAINEE)


@pytest.fixture
def created(operator: Any, scenario: Scenario, trainee: User) -> dict[str, Any]:
    body = {"scenario_id": str(scenario.id), "trainee_id": str(trainee.id)}
    response = operator.request("POST", DEPLOYMENTS, json=body)
    assert response.status_code == 201
    answer: dict[str, Any] = response.json()
    return answer


def issue(operator: Any, deployment_id: Any, owner: User) -> Any:
    path = f"{DEPLOYMENTS}/{deployment_id}/links"
    return operator.request("POST", path, json={"user_id": str(owner.id)})


def open_link(api: TestClient, token: str) -> httpx.Response:
    response: httpx.Response = api.get(f"/s/{token}", follow_redirects=False)
    return response


def test_create_returns_the_trainee_link_with_its_token(
    session: Session, created: dict[str, Any], trainee: User
) -> None:
    link = created["link"]

    assert set(link) == {"id", "user_id", "token"}
    assert link["user_id"] == str(trainee.id)
    stored = Link(session=session).load(deployment_id=uuid.UUID(created["id"]))
    assert str(stored.id) == link["id"]
    assert stored.token_hash == hashlib.sha256(link["token"].encode()).hexdigest()
    assert stored.token is None
    assert (stored.opened_at, stored.revoked_at) == (None, None)


@pytest.mark.parametrize("role", [Role.OPERATOR, Role.VIEWER])
def test_a_link_is_issued_for_an_operator_and_a_viewer(
    operator: Any, session: Session, created: dict[str, Any], role: Role
) -> None:
    owner = user(session, "delta", role)

    response = issue(operator, created["id"], owner)

    assert response.status_code == 201
    link = response.json()
    assert set(link) == LISTED | {"token"}
    assert (link["user_id"], link["role"]) == (str(owner.id), role)
    stored = Link(session=session).load(id=uuid.UUID(link["id"]))
    assert stored.token_hash == hashlib.sha256(link["token"].encode()).hexdigest()
    assert link["token"] != created["link"]["token"]


def test_the_list_shows_roles_and_no_token(
    operator: Any, session: Session, created: dict[str, Any], trainee: User
) -> None:
    viewer = user(session, "delta", Role.VIEWER)
    issued = issue(operator, created["id"], viewer).json()

    response = operator.request("GET", f"{DEPLOYMENTS}/{created['id']}/links")

    assert response.status_code == 200
    rows = response.json()
    assert all(set(row) == LISTED for row in rows)
    assert {(row["id"], row["user_id"], row["role"]) for row in rows} == {
        (created["link"]["id"], str(trainee.id), "trainee"),
        (issued["id"], str(viewer.id), "viewer"),
    }
    assert created["link"]["token"] not in response.text
    assert issued["token"] not in response.text
    hashes = [link.token_hash for link in Link._load(session)]
    assert len(hashes) == 2
    assert not any(token_hash in response.text for token_hash in hashes)


def test_revoke_stamps_once(operator: Any, session: Session, created: dict[str, Any]) -> None:
    path = f"{LINKS}/{created['link']['id']}/revoke"

    first = operator.request("POST", path)

    assert first.status_code == 200
    assert set(first.json()) == LISTED
    assert first.json()["role"] == "trainee"
    stamped = first.json()["revoked_at"]
    assert stamped is not None
    # Moved back, so a second stamp in the same second would still show.
    stored = Link(session=session).load(id=uuid.UUID(created["link"]["id"]))
    stored.revoked_at = stamped - 50
    stored.save()

    second = operator.request("POST", path)

    assert second.status_code == 200
    assert second.json()["revoked_at"] == stamped - 50


def test_a_new_trainee_link_is_issued_after_the_first_is_revoked(
    operator: Any, session: Session, created: dict[str, Any], trainee: User
) -> None:
    other = user(session, "delta", Role.TRAINEE)
    assert issue(operator, created["id"], trainee).status_code == 409
    assert issue(operator, created["id"], other).status_code == 409
    assert operator.request("POST", f"{LINKS}/{created['link']['id']}/revoke").is_success

    response = issue(operator, created["id"], other)

    assert response.status_code == 201
    assert response.json()["role"] == "trainee"
    assert issue(operator, created["id"], trainee).status_code == 409
    assert len(Link._load(session)) == 2


def test_create_without_a_trainee_is_unprocessable(
    operator: Any, session: Session, scenario: Scenario
) -> None:
    response = operator.request("POST", DEPLOYMENTS, json={"scenario_id": str(scenario.id)})

    assert response.status_code == 422
    assert Deployment._load(session) == []


@pytest.mark.parametrize(
    ("role", "state"),
    [
        (Role.VIEWER, State.ENABLED),
        (Role.OPERATOR, State.ENABLED),
        (Role.TRAINEE, State.DISABLED),
        (None, None),
    ],
)
def test_an_unusable_trainee_is_refused(
    operator: Any, session: Session, scenario: Scenario, role: Role | None, state: State | None
) -> None:
    if role is None or state is None:
        trainee_id = uuid.uuid4()
    else:
        trainee_id = user(session, "delta", role, state).id
    body = {"scenario_id": str(scenario.id), "trainee_id": str(trainee_id)}

    response = operator.request("POST", DEPLOYMENTS, json=body)

    assert response.status_code == 422
    assert Deployment._load(session) == []
    assert Link._load(session) == []


def test_a_link_that_cannot_be_saved_stops_the_deployment(
    operator: Any,
    session: Session,
    scenario: Scenario,
    trainee: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_: Link) -> None:
        raise ConflictError("the link is refused")

    monkeypatch.setattr(Link, "save", refuse)
    body = {"scenario_id": str(scenario.id), "trainee_id": str(trainee.id)}

    response = operator.request("POST", DEPLOYMENTS, json=body)

    assert response.status_code == 409
    assert [deployment.status for deployment in Deployment._load(session)] == [S.CANCELLED]
    assert Link._load(session) == []


@pytest.mark.parametrize("known", [True, False])
def test_a_link_for_an_unknown_or_disabled_user_is_refused(
    operator: Any, session: Session, created: dict[str, Any], known: bool
) -> None:
    owner_id = user(session, "delta", Role.VIEWER, State.DISABLED).id if known else uuid.uuid4()
    path = f"{DEPLOYMENTS}/{created['id']}/links"

    response = operator.request("POST", path, json={"user_id": str(owner_id)})

    assert response.status_code == 422
    assert len(Link._load(session)) == 1


@pytest.mark.parametrize("status", [S.COMPLETED, S.FAILED, S.CANCELLED])
def test_issue_on_a_terminal_deployment_conflicts(
    operator: Any, session: Session, scenario: Scenario, status: S
) -> None:
    deployment = deploy(session, scenario, status)

    response = issue(operator, deployment.id, user(session, "delta", Role.VIEWER))

    assert response.status_code == 409
    assert Link._load(session) == []


def test_a_malformed_issue_is_unprocessable(operator: Any, created: dict[str, Any]) -> None:
    path = f"{DEPLOYMENTS}/{created['id']}/links"
    body = {"user_id": created["link"]["user_id"], "role": "operator"}

    assert operator.request("POST", path, json=body).status_code == 422
    assert operator.request("POST", path, json={}).status_code == 422


def test_an_unknown_deployment_or_link_is_not_found(operator: Any, trainee: User) -> None:
    unknown = f"{DEPLOYMENTS}/{uuid.uuid4()}/links"

    assert operator.request("GET", unknown).status_code == 404
    assert issue(operator, uuid.uuid4(), trainee).status_code == 404
    assert operator.request("POST", f"{LINKS}/{uuid.uuid4()}/revoke").status_code == 404


def test_open_redirects_without_the_token_and_sets_the_scoped_cookie(
    api: TestClient, created: dict[str, Any]
) -> None:
    token = created["link"]["token"]

    response = open_link(api, token)

    assert response.status_code == 303
    assert response.headers["location"] == f"/t/{created['id']}"
    assert token not in response.headers["location"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert len(response.headers.get_list("set-cookie")) == 1
    value, *attributes = (part.strip() for part in response.headers["set-cookie"].split(";"))
    name, _, session_token = value.partition("=")
    assert name == "jatszoter_link"
    assert session_token not in ("", token)
    # Exactly these, so no Expires and no Max-Age either.
    assert {attribute.lower() for attribute in attributes} == {
        "httponly",
        "secure",
        "samesite=strict",
        f"path=/api/v1/deployments/{created['id']}",
    }


def test_opened_at_is_stamped_once_across_two_opens(
    api: TestClient, session: Session, created: dict[str, Any]
) -> None:
    token = created["link"]["token"]
    first = open_link(api, token)
    stored = Link(session=session).load(id=uuid.UUID(created["link"]["id"]))
    stamped = stored.opened_at
    assert stamped is not None
    # Moved back, so a second stamp in the same second would still show.
    stored.opened_at = stamped - 50
    stored.save()

    second = open_link(api, token)

    assert second.status_code == 303
    assert second.headers["set-cookie"] != first.headers["set-cookie"]
    assert stored.opened_at == stamped - 50


def test_the_access_log_redacts_the_token(api: TestClient) -> None:
    token = secrets.token_urlsafe(32)
    logger = logging.getLogger("uvicorn.access")
    record = logger.makeRecord(
        logger.name,
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1", "GET", f"/s/{token}?alpha=1", "1.1", 303),
        None,
    )

    assert logger.filter(record)

    assert token not in record.getMessage()
    assert '"GET /s/<redacted>?alpha=1 HTTP/1.1" 303' in record.getMessage()


def test_an_unknown_revoked_or_disabled_link_gets_the_same_404_and_no_cookie(
    api: TestClient, operator: Any, session: Session, created: dict[str, Any]
) -> None:
    viewer = user(session, "delta", Role.VIEWER)
    issued = issue(operator, created["id"], viewer).json()
    viewer.state = State.DISABLED
    viewer.save()
    assert operator.request("POST", f"{LINKS}/{created['link']['id']}/revoke").is_success

    refusals = [
        open_link(api, secrets.token_urlsafe(32)),
        open_link(api, created["link"]["token"]),
        open_link(api, issued["token"]),
    ]

    assert [response.status_code for response in refusals] == [404, 404, 404]
    assert len({response.content for response in refusals}) == 1
    assert not any("set-cookie" in response.headers for response in refusals)
    assert [link.opened_at for link in Link._load(session)] == [None, None]


def test_the_first_open_of_a_trainee_link_starts_one_recording(
    api: TestClient, session: Session, created: dict[str, Any]
) -> None:
    token = created["link"]["token"]

    assert open_link(api, token).status_code == 303
    assert open_link(api, token).status_code == 303

    [recording] = Recording._load(session)
    assert str(recording.link_id) == created["link"]["id"]
    assert str(recording.deployment_id) == created["id"]
    assert (recording.size_bytes, recording.duration_seconds) == (0, 0.0)
    stored = File(recording.path)
    stored.get()
    [header] = stored.data.decode().splitlines()
    assert json.loads(header) == {
        "version": 2,
        "width": 80,
        "height": 24,
        "timestamp": recording.created_at,
        "title": f"nginx-502 v1 {created['id']}",
    }
    assert token not in stored.data.decode()


@pytest.mark.parametrize("role", [Role.OPERATOR, Role.VIEWER])
def test_an_operator_or_viewer_link_starts_no_recording(
    api: TestClient, operator: Any, session: Session, created: dict[str, Any], role: Role
) -> None:
    issued = issue(operator, created["id"], user(session, "delta", role)).json()

    assert open_link(api, issued["token"]).status_code == 303

    assert Recording._load(session) == []


def test_revoking_a_trainee_link_closes_its_recording(
    api: TestClient, operator: Any, session: Session, created: dict[str, Any]
) -> None:
    open_link(api, created["link"]["token"])
    [recording] = Recording._load(session)
    recording.created_at -= 5
    recording.save()

    assert operator.request("POST", f"{LINKS}/{created['link']['id']}/revoke").is_success

    session.refresh(recording)
    stored = File(recording.path)
    stored.get()
    assert recording.size_bytes == len(stored.data) > 0
    assert recording.duration_seconds >= 5


def test_a_link_whose_recording_cannot_start_still_opens(
    api: TestClient, created: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: File) -> None:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(File, "append", refuse)

    response = open_link(api, created["link"]["token"])

    assert response.status_code == 303
    assert "set-cookie" in response.headers
