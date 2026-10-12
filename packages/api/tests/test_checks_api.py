import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from jatszoter_api.file import File
from jatszoter_api.lifecycle import DeploymentStatus
from jatszoter_api.models import (
    Check,
    CheckStatus,
    Deployment,
    Image,
    Link,
    Role,
    Scenario,
    State,
    User,
)

S = DeploymentStatus
DEPLOYMENTS = "/api/v1/deployments"
CHECK_SECONDS = 30
SHOWN = {"id", "status", "exit_code", "message", "created_at", "finished_at"}


@pytest.fixture
def deploy(session: Session) -> Callable[..., Deployment]:
    Image(session=session, name="debian-12", url="https://example.com/d", digest="a" * 64).save()
    scenario = Scenario(
        session=session,
        slug="nginx-502",
        version=1,
        document={"image": "debian-12"},
        digest="b" * 64,
    )
    scenario.save(File(scenario.bundle_path, b"bundle"))

    def deployed(status: S = S.READY) -> Deployment:
        deployment = Deployment(
            session=session,
            scenario_id=scenario.id,
            image_id=scenario.image.id,
            vm_id=uuid.uuid4(),
            status=status,
            cpus=1,
            memory_mib=512,
            disk_gib=4,
            bootstrap_seconds=600,
            check_seconds=CHECK_SECONDS,
            session_minutes=60,
        )
        deployment.save()
        return deployment

    return deployed


@pytest.fixture
def ready(deploy: Callable[..., Deployment]) -> Deployment:
    return deploy()


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


def path(deployment_id: Any, check_id: Any = None) -> str:
    checks = f"{DEPLOYMENTS}/{deployment_id}/checks"
    return checks if check_id is None else f"{checks}/{check_id}"


def linked(api: TestClient, method: str, where: str, cookie: str, **extra: str) -> httpx.Response:
    answer: httpx.Response = api.request(method, where, headers={"Cookie": cookie, **extra})
    return answer


def held(session: Session, deployment: Deployment, age: int = 0, **fields: Any) -> Check:
    """A stored check of `deployment`, requested `age` seconds ago."""
    check = Check(
        session=session,
        deployment_id=deployment.id,
        user_id=uuid.uuid4(),
        created_at=int(time.time()) - age,
        **fields,
    )
    check.save()
    return check


def test_a_trainee_link_stores_a_pending_check(
    api: TestClient, session: Session, ready: Deployment
) -> None:
    link, cookie = opened(api, session, ready, Role.TRAINEE)

    response = linked(api, "POST", path(ready.id), cookie, Origin="http://testserver")

    assert response.status_code == 201
    answer = response.json()
    assert set(answer) == SHOWN
    assert (answer["status"], answer["exit_code"], answer["finished_at"]) == ("pending", None, None)
    [stored] = Check._load(session, deployment_id=ready.id)
    assert (str(stored.id), stored.link_id, stored.user_id) == (
        answer["id"],
        link.id,
        link.user_id,
    )


def test_a_signed_operator_stores_a_pending_check(
    operator: Any, session: Session, ready: Deployment
) -> None:
    response = operator.request("POST", path(ready.id))

    assert response.status_code == 201
    assert set(response.json()) == SHOWN | {"detail"}
    [stored] = Check._load(session, deployment_id=ready.id)
    assert (stored.link_id, str(stored.user_id)) == (None, operator.id)
    assert stored.status is CheckStatus.PENDING


def test_an_operator_link_requests_and_sees_the_detail(
    api: TestClient, session: Session, ready: Deployment
) -> None:
    link, cookie = opened(api, session, ready, Role.OPERATOR)

    response = linked(api, "POST", path(ready.id), cookie)

    assert response.status_code == 201
    assert set(response.json()) == SHOWN | {"detail"}
    assert Check(session=session).load(deployment_id=ready.id).link_id == link.id


def test_the_list_is_oldest_first_and_one_check_reads_alone(
    operator: Any, session: Session, ready: Deployment
) -> None:
    newer = held(session, ready, age=10, status=CheckStatus.PASSED, exit_code=0)
    older = held(session, ready, age=20, status=CheckStatus.FAILED, exit_code=1)

    rows = operator.request("GET", path(ready.id)).json()
    one = operator.request("GET", path(ready.id, older.id))

    assert [row["id"] for row in rows] == [str(older.id), str(newer.id)]
    assert one.status_code == 200
    assert (one.json()["status"], one.json()["exit_code"]) == ("failed", 1)


def test_an_overdue_pending_check_reads_as_timeout_and_frees_the_next_request(
    operator: Any, session: Session, ready: Deployment
) -> None:
    overdue = held(session, ready, age=CHECK_SECONDS + 31)

    [row] = operator.request("GET", path(ready.id)).json()
    again = operator.request("POST", path(ready.id))

    assert (row["id"], row["status"], row["exit_code"]) == (str(overdue.id), "timeout", None)
    assert row["finished_at"] is not None
    assert again.status_code == 201
    assert again.json()["id"] != str(overdue.id)


def test_a_request_itself_settles_an_overdue_check(
    operator: Any, session: Session, ready: Deployment
) -> None:
    overdue = held(session, ready, age=CHECK_SECONDS + 31)

    assert operator.request("POST", path(ready.id)).status_code == 201

    session.refresh(overdue)
    assert overdue.status is CheckStatus.TIMEOUT


def test_the_operator_sees_the_detail_and_a_trainee_or_viewer_link_has_no_such_key(
    api: TestClient, operator: Any, session: Session, ready: Deployment
) -> None:
    check = held(session, ready)
    assert check.settle(CheckStatus.FAILED, 1, "alpha", "beta")
    _, trainee = opened(api, session, ready, Role.TRAINEE)
    _, viewer = opened(api, session, ready, Role.VIEWER, name="gamma")

    signed = operator.request("GET", path(ready.id, check.id)).json()

    assert (signed["message"], signed["detail"]) == ("alpha", "beta")
    for cookie in (trainee, viewer):
        one = linked(api, "GET", path(ready.id, check.id), cookie)
        [row] = linked(api, "GET", path(ready.id), cookie).json()
        assert one.status_code == 200
        assert set(one.json()) == set(row) == SHOWN
        assert one.json()["message"] == "alpha"
        assert "beta" not in one.text


def test_a_result_is_cut_to_its_limits_and_stored_once(session: Session, ready: Deployment) -> None:
    check = held(session, ready)

    first = check.settle(CheckStatus.FAILED, 3, "a" * 300 + "\nbeta", "\u00e9" * 4096)
    second = check.settle(CheckStatus.PASSED, 0, "gamma")

    assert (first, second) == (True, False)
    stored = Check(session=session).load(id=check.id)
    assert (stored.status, stored.exit_code, stored.message) == (CheckStatus.FAILED, 3, "a" * 200)
    assert stored.detail == "\u00e9" * 2048
    assert stored.finished_at is not None


def test_a_second_request_while_one_is_pending_conflicts(
    operator: Any, session: Session, ready: Deployment
) -> None:
    held(session, ready, age=CHECK_SECONDS + 29)

    assert operator.request("POST", path(ready.id)).status_code == 409
    assert len(Check._load(session, deployment_id=ready.id)) == 1


@pytest.mark.parametrize("status", [S.PENDING, S.BOOTSTRAPPING, S.STOPPING, S.COMPLETED])
def test_a_request_is_409_unless_the_deployment_is_ready(
    operator: Any, session: Session, deploy: Callable[..., Deployment], status: S
) -> None:
    waiting = deploy(status=status)

    assert operator.request("POST", path(waiting.id)).status_code == 409
    assert Check._load(session, deployment_id=waiting.id) == []


def test_a_viewer_link_reads_and_requests_nothing(
    api: TestClient, session: Session, ready: Deployment
) -> None:
    _, cookie = opened(api, session, ready, Role.VIEWER)

    assert linked(api, "POST", path(ready.id), cookie).status_code == 403
    assert linked(api, "GET", path(ready.id), cookie).json() == []
    assert Check._load(session, deployment_id=ready.id) == []


def test_everyone_but_a_party_of_the_deployment_is_refused(
    api: TestClient,
    sign_in: Callable[..., Any],
    session: Session,
    deploy: Callable[..., Deployment],
    ready: Deployment,
) -> None:
    other = deploy()
    check = held(session, ready, status=CheckStatus.PASSED, exit_code=0)
    _, cookie = opened(api, session, other, Role.TRAINEE)
    _, own = opened(api, session, ready, Role.TRAINEE, name="gamma")
    trainee = sign_in("delta", Role.TRAINEE)
    api.cookies.clear()

    for method, where in (
        ("POST", path(ready.id)),
        ("GET", path(ready.id)),
        ("GET", path(ready.id, check.id)),
    ):
        assert api.request(method, where).status_code == 401
        assert linked(api, method, where, cookie).status_code == 403
        assert linked(api, method, where, own, Origin="https://example.com").status_code == 403
        assert trainee.request(method, where).status_code == 403
    assert len(Check._load(session, deployment_id=ready.id)) == 1


def test_an_unknown_deployment_or_check_is_not_found(
    operator: Any, session: Session, deploy: Callable[..., Deployment], ready: Deployment
) -> None:
    elsewhere = held(session, deploy(), status=CheckStatus.PASSED, exit_code=0)

    assert operator.request("POST", path(uuid.uuid4())).status_code == 404
    assert operator.request("GET", path(uuid.uuid4())).status_code == 404
    assert operator.request("GET", path(ready.id, uuid.uuid4())).status_code == 404
    assert operator.request("GET", path(ready.id, elsewhere.id)).status_code == 404
