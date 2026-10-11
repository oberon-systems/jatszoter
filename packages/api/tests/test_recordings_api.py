import json
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from jatszoter_api.file import File
from jatszoter_api.lifecycle import Actor, DeploymentStatus
from jatszoter_api.models import Deployment, Image, Recording, Role, Scenario, State, User

S = DeploymentStatus
DEPLOYMENTS = "/api/v1/deployments"
RECORDINGS = "/api/v1/recordings"
LISTED = {"id", "link_id", "created_at", "size_bytes", "duration_seconds"}


@pytest.fixture
def created(operator: Any, session: Session) -> dict[str, Any]:
    """A deployment made through the API, with its unopened trainee link."""
    Image(session=session, name="debian-12", url="https://example.com/d", digest="a" * 64).save()
    scenario = Scenario(
        session=session,
        slug="nginx-502",
        version=1,
        document={"id": "nginx-502", "version": 1, "title": "alpha", "image": "debian-12"},
        digest="b" * 64,
    )
    scenario.save(File(scenario.bundle_path, b"bundle"))
    trainee = User(session=session, name="gamma", role=Role.TRAINEE, state=State.ENABLED)
    trainee.save()
    body = {"scenario_id": str(scenario.id), "trainee_id": str(trainee.id)}
    response = operator.request("POST", DEPLOYMENTS, json=body)
    assert response.status_code == 201
    answer: dict[str, Any] = response.json()
    return answer


def open_link(api: TestClient, token: str) -> str:
    """Open a link; the answer is its session cookie."""
    response = api.get(f"/s/{token}", follow_redirects=False)
    assert response.status_code == 303
    cookie: str = response.headers["set-cookie"].partition(";")[0]
    return cookie


def listed(operator: Any, deployment_id: Any) -> httpx.Response:
    answer: httpx.Response = operator.request("GET", f"{DEPLOYMENTS}/{deployment_id}/recordings")
    return answer


def download(operator: Any, recording_id: Any) -> httpx.Response:
    answer: httpx.Response = operator.request("GET", f"{RECORDINGS}/{recording_id}.cast")
    return answer


def raw(recording: Recording) -> bytes:
    stored = File(recording.path)
    stored.get()
    return stored.data


def test_the_listing_shows_a_live_recording_then_its_size_after_a_revoke(
    api: TestClient, operator: Any, session: Session, created: dict[str, Any]
) -> None:
    open_link(api, created["link"]["token"])

    live = listed(operator, created["id"])

    assert live.status_code == 200
    [row] = live.json()
    assert set(row) == LISTED
    assert row["link_id"] == created["link"]["id"]
    assert (row["size_bytes"], row["duration_seconds"]) == (0, 0.0)

    revoke = f"/api/v1/links/{created['link']['id']}/revoke"
    assert operator.request("POST", revoke).is_success

    [closed] = listed(operator, created["id"]).json()
    [recording] = Recording._load(session)
    assert closed["id"] == row["id"] == str(recording.id)
    assert closed["size_bytes"] == len(raw(recording)) > 0
    assert recording.url not in live.text


def test_the_listing_is_oldest_first(
    operator: Any, session: Session, created: dict[str, Any]
) -> None:
    deployment_id = uuid.UUID(created["id"])
    newer = Recording(
        session=session, deployment_id=deployment_id, link_id=uuid.uuid4(), created_at=200
    )
    older = Recording(
        session=session, deployment_id=deployment_id, link_id=uuid.uuid4(), created_at=100
    )
    newer.save()
    older.save()
    Recording(session=session, deployment_id=uuid.uuid4(), link_id=uuid.uuid4()).save()

    rows = listed(operator, deployment_id).json()

    assert [row["id"] for row in rows] == [str(older.id), str(newer.id)]


def test_the_download_of_a_live_recording_starts_with_the_header(
    api: TestClient, operator: Any, session: Session, created: dict[str, Any]
) -> None:
    open_link(api, created["link"]["token"])
    [recording] = Recording._load(session)

    response = download(operator, recording.id)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-asciicast"
    assert response.headers["cache-control"] == "no-store"
    header = json.loads(response.content.splitlines()[0])
    assert (header["version"], header["width"], header["height"]) == (2, 80, 24)


def test_the_download_after_the_deployment_ended_returns_the_file(
    api: TestClient, operator: Any, session: Session, created: dict[str, Any]
) -> None:
    open_link(api, created["link"]["token"])
    [recording] = Recording._load(session)
    deployment = Deployment(session=session).load(id=uuid.UUID(created["id"]))

    deployment.transition("cancel", S.PENDING, actor=Actor.OPERATOR)

    response = download(operator, recording.id)
    assert response.status_code == 200
    assert response.content == raw(recording)
    [row] = listed(operator, created["id"]).json()
    assert row["size_bytes"] == len(response.content)


@pytest.mark.parametrize("role", [Role.TRAINEE, Role.VIEWER, Role.OPERATOR])
def test_both_routes_refuse_an_unsigned_request_and_a_link_cookie(
    api: TestClient, operator: Any, session: Session, created: dict[str, Any], role: Role
) -> None:
    token = created["link"]["token"]
    if role is not Role.TRAINEE:
        owner = User(session=session, name="delta", role=role, state=State.ENABLED)
        owner.save()
        path = f"{DEPLOYMENTS}/{created['id']}/links"
        token = operator.request("POST", path, json={"user_id": str(owner.id)}).json()["token"]
    open_link(api, created["link"]["token"])
    cookie = open_link(api, token)
    [recording] = Recording._load(session)
    paths = [f"{DEPLOYMENTS}/{created['id']}/recordings", f"{RECORDINGS}/{recording.id}.cast"]
    api.cookies.clear()

    for path in paths:
        unsigned = api.get(path)
        linked = api.get(path, headers={"Cookie": cookie})
        elsewhere = api.get(DEPLOYMENTS, headers={"Cookie": cookie})

        assert unsigned.status_code == 401
        assert linked.status_code == elsewhere.status_code == 401
        assert b"version" not in linked.content


def test_a_signed_trainee_is_forbidden(
    api: TestClient, sign_in: Any, session: Session, created: dict[str, Any]
) -> None:
    open_link(api, created["link"]["token"])
    [recording] = Recording._load(session)
    trainee = sign_in("delta", Role.TRAINEE)

    assert listed(trainee, created["id"]).status_code == 403
    assert download(trainee, recording.id).status_code == 403


def test_an_unknown_recording_or_deployment_is_not_found(
    operator: Any, session: Session, created: dict[str, Any]
) -> None:
    gone = Recording(session=session, deployment_id=uuid.UUID(created["id"]), link_id=uuid.uuid4())
    gone.save()

    assert download(operator, uuid.uuid4()).status_code == 404
    assert download(operator, "alpha").status_code == 404
    assert download(operator, gone.id).status_code == 404
    assert listed(operator, uuid.uuid4()).status_code == 404


def test_a_deployment_whose_link_was_never_opened_lists_nothing(
    operator: Any, created: dict[str, Any]
) -> None:
    response = listed(operator, created["id"])

    assert (response.status_code, response.json()) == (200, [])
