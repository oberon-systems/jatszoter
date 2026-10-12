import hashlib
import uuid
from typing import Any

import httpx
import pytest
from sqlmodel import Session

from jatszoter_api.errors import InvalidTransitionError
from jatszoter_api.file import File
from jatszoter_api.lifecycle import Actor, DeploymentStatus
from jatszoter_api.models import (
    Check,
    CheckStatus,
    Deployment,
    Image,
    Link,
    Recording,
    Role,
    Scenario,
    User,
)
from jatszoter_api.models.recording import live

S = DeploymentStatus
AGENT = uuid.uuid4()
OTHER_AGENT = uuid.uuid4()
SCENARIO = uuid.uuid4()
FIELDS: dict[str, Any] = {
    "scenario_id": SCENARIO,
    "image_id": uuid.uuid4(),
    "cpus": 1,
    "memory_mib": 512,
    "disk_gib": 4,
    "bootstrap_seconds": 600,
    "check_seconds": 30,
    "session_minutes": 60,
}


def deploy(
    session: Session, status: S = S.PENDING, agent_id: uuid.UUID | None = AGENT
) -> Deployment:
    trainee = User(session=session, name="alpha", role=Role.TRAINEE)
    trainee.save()
    deployment = Deployment(session=session, status=status, agent_id=agent_id, **FIELDS)
    deployment.save()
    Link(session=session, deployment_id=deployment.id, user_id=trainee.id).save()
    return deployment


def test_saving_stores_the_deployment_and_its_trainee_link(session: Session) -> None:
    trainee = User(session=session, name="alpha", role=Role.TRAINEE)
    trainee.save()
    deployment = Deployment(session=session, **FIELDS)
    deployment.save()
    issued = Link(session=session, deployment_id=deployment.id, user_id=trainee.id)
    issued.save()
    assert issued.token is not None
    token = issued.token
    deployment_id, trainee_id = deployment.id, trainee.id
    session.expunge_all()

    stored = deployment.load(id=deployment_id)
    [link] = stored.links
    assert stored.status is S.PENDING
    assert link.user_id == trainee_id
    assert link.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert link.revoked_at is None
    assert link.token is None
    with pytest.raises(IndexError):
        _ = link.recording


def test_loading_by_id_returns_the_stored_row(session: Session) -> None:
    deployment = deploy(session, S.READY)

    assert deployment.load(id=deployment.id) is deployment


def test_loading_an_unknown_id_fails(session: Session) -> None:
    deployment = deploy(session)

    with pytest.raises(IndexError):
        deployment.load(id=uuid.uuid4())


def test_agent_moves_its_deployment(session: Session) -> None:
    deployment = deploy(session, S.BOOTSTRAPPING)

    deployment.transition("bootstrap", S.BOOTSTRAPPING, actor=Actor.AGENT, agent_id=AGENT)

    assert deployment.status is S.READY
    assert deployment.ready_at is not None


def test_terminal_move_stamps_the_end(session: Session) -> None:
    deployment = deploy(session, agent_id=None)

    deployment.transition("cancel", S.PENDING, actor=Actor.OPERATOR)

    assert deployment.status is S.CANCELLED
    assert deployment.ended_at is not None


def test_stale_status_conflicts(session: Session) -> None:
    deployment = deploy(session, S.READY)

    with pytest.raises(InvalidTransitionError):
        deployment.transition("boot", S.STARTING, actor=Actor.AGENT, agent_id=AGENT)


def test_other_agent_conflicts(session: Session) -> None:
    deployment = deploy(session, S.STARTING)

    with pytest.raises(InvalidTransitionError):
        deployment.transition("boot", S.STARTING, actor=Actor.AGENT, agent_id=OTHER_AGENT)


def test_repeated_move_is_a_no_op(session: Session) -> None:
    deployment = deploy(session, S.STOPPING)

    deployment.transition("destroy", S.STOPPING, actor=Actor.AGENT, agent_id=AGENT)
    ended_at = deployment.ended_at
    deployment.transition("destroy", S.STOPPING, actor=Actor.AGENT, agent_id=AGENT)

    assert deployment.status is S.COMPLETED
    assert deployment.ended_at == ended_at


def test_failed_needs_a_reason(session: Session) -> None:
    deployment = deploy(session, S.READY)

    with pytest.raises(ValueError, match="reason"):
        deployment.transition("abort", S.READY, actor=Actor.API)


def test_agent_move_names_its_agent(session: Session) -> None:
    deployment = deploy(session, S.STARTING)

    with pytest.raises(ValueError, match="agent"):
        deployment.transition("boot", S.STARTING, actor=Actor.AGENT)


def test_wrong_actor_is_refused_before_any_write(session: Session) -> None:
    deployment = deploy(session, S.READY)

    with pytest.raises(InvalidTransitionError):
        deployment.transition("stop", S.READY, actor=Actor.AGENT)

    session.refresh(deployment)
    assert deployment.status is S.READY


def test_ending_a_deployment_revokes_every_link(session: Session) -> None:
    deployment = deploy(session, S.STOPPING)
    viewer = User(session=session, name="beta", role=Role.VIEWER)
    viewer.save()
    Link(session=session, deployment_id=deployment.id, user_id=viewer.id).save()

    deployment.transition("destroy", S.STOPPING, actor=Actor.AGENT, agent_id=AGENT)

    assert len(deployment.links) == 2
    assert all(link.revoked_at is not None for link in deployment.links)


def test_completing_a_deployment_closes_its_recording(session: Session) -> None:
    deployment = deploy(session, S.STOPPING)
    [link] = deployment.links
    recording = Recording(session=session, deployment_id=deployment.id, link_id=link.id)
    recording.start(deployment.console, "alpha v1", None, 1024)

    deployment.transition("destroy", S.STOPPING, actor=Actor.AGENT, agent_id=AGENT)

    assert deployment.status is S.COMPLETED
    assert Recording(session=session).load(id=recording.id).size_bytes > 0
    assert live(deployment.id) is None


def test_a_recording_that_cannot_be_closed_does_not_stop_the_transition(
    session: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    deployment = deploy(session, S.STOPPING)
    [link] = deployment.links
    recording = Recording(session=session, deployment_id=deployment.id, link_id=link.id)
    recording.start(deployment.console, "alpha v1", None, 1024)

    def refuse(self: File) -> int:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(File, "size", refuse)

    deployment.transition("destroy", S.STOPPING, actor=Actor.AGENT, agent_id=AGENT)

    assert deployment.status is S.COMPLETED
    assert link.revoked_at is not None
    assert Recording(session=session).load(id=recording.id).size_bytes == 0
    assert f"recording of deployment {deployment.id} not closed: ConnectError" in caplog.text


def test_ending_a_deployment_leaves_no_pending_check(session: Session) -> None:
    deployment = deploy(session, S.READY)
    passed = Check(
        session=session,
        deployment_id=deployment.id,
        user_id=uuid.uuid4(),
        status=CheckStatus.PASSED,
        exit_code=0,
        message="alpha",
    )
    passed.save()
    pending = Check(session=session, deployment_id=deployment.id, user_id=uuid.uuid4())
    pending.save()

    deployment.transition("abort", S.READY, "lease expired", actor=Actor.API)

    stored = Check(session=session).load(id=pending.id)
    assert (stored.status, stored.message) == (CheckStatus.ERROR, "deployment ended")
    assert stored.finished_at is not None
    kept = Check(session=session).load(id=passed.id)
    assert (kept.status, kept.message) == (CheckStatus.PASSED, "alpha")


def test_a_live_deployment_keeps_its_links(session: Session) -> None:
    deployment = deploy(session, S.BOOTSTRAPPING)

    deployment.transition("bootstrap", S.BOOTSTRAPPING, actor=Actor.AGENT, agent_id=AGENT)

    [link] = deployment.links
    assert link.revoked_at is None


@pytest.mark.parametrize(
    ("status", "target"),
    [
        (S.PENDING, S.CANCELLED),
        (S.STARTING, S.STOPPING),
        (S.BOOTSTRAPPING, S.STOPPING),
        (S.READY, S.STOPPING),
    ],
)
def test_stop_lands_in_the_right_state(session: Session, status: S, target: S) -> None:
    deployment = deploy(session, status)

    deployment.stop()

    session.refresh(deployment)
    assert deployment.status is target


@pytest.mark.parametrize("status", [S.STOPPING, S.CANCELLED])
def test_a_repeated_stop_changes_nothing(session: Session, status: S) -> None:
    deployment = deploy(session, status)
    updated_at = deployment.updated_at

    deployment.stop()

    session.refresh(deployment)
    assert deployment.status is status
    assert deployment.updated_at == updated_at


@pytest.mark.parametrize("status", [S.COMPLETED, S.FAILED])
def test_stopping_an_ended_deployment_conflicts(session: Session, status: S) -> None:
    deployment = deploy(session, status)

    with pytest.raises(InvalidTransitionError):
        deployment.stop()


def test_the_agent_cannot_stop_and_the_operator_cannot_boot(session: Session) -> None:
    deployment = deploy(session, S.STARTING)

    with pytest.raises(InvalidTransitionError):
        deployment.transition("stop", S.STARTING, actor=Actor.AGENT, agent_id=AGENT)
    with pytest.raises(InvalidTransitionError):
        deployment.transition("boot", S.STARTING, actor=Actor.OPERATOR)

    session.refresh(deployment)
    assert deployment.status is S.STARTING


def test_the_scenario_and_its_image_load_from_the_deployment(session: Session) -> None:
    image = Image(session=session, name="debian-12", url="https://example.com/d", digest="a" * 64)
    image.save()
    scenario = Scenario(
        session=session,
        slug="nginx-502",
        version=1,
        document={"id": "nginx-502", "version": 1, "image": "debian-12"},
        digest="b" * 64,
    )
    scenario.save(File(scenario.bundle_path, b"bundle"))
    stored = Deployment(
        session=session, **{**FIELDS, "scenario_id": scenario.id, "image_id": image.id}
    )
    stored.save()

    deployment = Deployment(session=session).load(id=stored.id)

    assert deployment.scenario.id == scenario.id
    assert deployment.image.id == image.id
    assert deployment.scenario.image.id == image.id
