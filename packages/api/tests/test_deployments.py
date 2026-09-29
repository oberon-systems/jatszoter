import hashlib
import uuid

import pytest
from sqlmodel import Session

from jatszoter_api.errors import InvalidTransitionError, NotFoundError
from jatszoter_api.lifecycle import Actor, DeploymentStatus
from jatszoter_api.models import Deployment, Link, Role, User

S = DeploymentStatus
AGENT = uuid.uuid4()
OTHER_AGENT = uuid.uuid4()
SCENARIO = uuid.uuid4()


def deploy(
    session: Session, status: S = S.PENDING, agent_id: uuid.UUID | None = AGENT
) -> Deployment:
    trainee = User(name="alpha", role=Role.TRAINEE)
    deployment, _ = Deployment.register(
        trainee,
        scenario_id=SCENARIO,
        agent_id=agent_id,
        status=status,
        cpus=1,
        memory_mib=512,
        disk_gib=4,
        bootstrap_seconds=600,
        check_seconds=30,
        session_minutes=60,
    )
    return deployment


def test_registering_stores_the_deployment_with_its_trainee_link(session: Session) -> None:
    trainee = User(name="alpha", role=Role.TRAINEE)
    deployment, token = Deployment.register(
        trainee,
        scenario_id=SCENARIO,
        cpus=1,
        memory_mib=512,
        disk_gib=4,
        bootstrap_seconds=600,
        check_seconds=30,
        session_minutes=60,
    )
    deployment_id, trainee_id = deployment.id, trainee.id
    session.expunge_all()

    stored = Deployment.get(id=deployment_id)
    [link] = stored.links
    assert stored.status is S.PENDING
    assert link.user_id == trainee_id
    assert link.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert link.revoked_at is None
    assert link.recording is None


def test_opening_by_id_returns_the_stored_row(session: Session) -> None:
    deployment = deploy(session, S.READY)

    assert Deployment.get(id=deployment.id) is deployment


def test_opening_an_unknown_id_fails(session: Session) -> None:
    with pytest.raises(NotFoundError):
        Deployment.get(id=uuid.uuid4())


def test_opening_by_a_unique_field_returns_the_stored_row(session: Session) -> None:
    deployment = deploy(session)
    [link] = deployment.links

    assert Link.get(token_hash=link.token_hash) is link


def test_a_deployment_needs_a_trainee(session: Session) -> None:
    operator = User(name="gamma", role=Role.OPERATOR)

    with pytest.raises(ValueError, match="trainee"):
        Deployment.register(operator, scenario_id=SCENARIO)


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
    viewer = User(name="beta", role=Role.VIEWER)
    viewer_link, _ = Link.issue(deployment.id, viewer.id)
    session.commit()

    deployment.transition("destroy", S.STOPPING, actor=Actor.AGENT, agent_id=AGENT)

    assert len(deployment.links) == 2
    assert all(link.revoked_at is not None for link in deployment.links)


def test_a_live_deployment_keeps_its_links(session: Session) -> None:
    deployment = deploy(session, S.BOOTSTRAPPING)

    deployment.transition("bootstrap", S.BOOTSTRAPPING, actor=Actor.AGENT, agent_id=AGENT)

    [link] = deployment.links
    assert link.revoked_at is None
