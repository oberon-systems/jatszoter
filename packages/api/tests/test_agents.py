import time
import uuid
from typing import Any

import pytest
from sqlmodel import Session, select

from jatszoter_api.errors import InvalidTransitionError
from jatszoter_api.lifecycle import DeploymentStatus
from jatszoter_api.models import Agent, Deployment, Link, Recording, State
from jatszoter_api.models._cache import Cached
from jatszoter_api.models.agent import LEASE_SECONDS

FIELDS: dict[str, Any] = {
    "scenario_id": uuid.uuid4(),
    "image_id": uuid.uuid4(),
    "cpus": 1,
    "memory_mib": 512,
    "disk_gib": 4,
    "bootstrap_seconds": 600,
    "check_seconds": 30,
    "session_minutes": 60,
}


def pending(
    session: Session, name: str = "alpha", auto_register: bool = False
) -> tuple[uuid.UUID, str]:
    agent = Agent(session=session, name=name, labels=["beta"])
    return agent.id, agent.claim_code(auto_register)


def approved(session: Session, name: str = "alpha") -> Agent:
    agent_id, _ = pending(session, name)
    agent = Agent(session=session, id=agent_id).approve()
    assert agent is not None
    return agent


def deploy(session: Session, agent: Agent, status: DeploymentStatus) -> Deployment:
    deployment = Deployment(session=session, status=status, agent_id=agent.id, **FIELDS)
    deployment.save()
    return deployment


def test_a_registration_waits_in_the_cache_only(session: Session) -> None:
    agent_id, code = pending(session)

    assert list(session.exec(select(Agent))) == []
    assert Cached._client().exists(f"registration:{agent_id}")
    with pytest.raises(InvalidTransitionError):
        Agent(session=session).claimed(code)


def test_approval_stores_the_agent_enabled(session: Session) -> None:
    agent_id, _ = pending(session)

    agent = Agent(session=session, id=agent_id).approve()

    assert agent is not None
    assert agent.id == agent_id
    assert agent.name == "alpha"
    assert agent.labels == ["beta"]
    assert agent.state is State.ENABLED
    assert not Cached._client().exists(f"registration:{agent_id}")


def test_a_registration_is_approved_once(session: Session) -> None:
    agent_id, _ = pending(session)
    Agent(session=session, id=agent_id).approve()

    assert Agent(session=session, id=agent_id).approve() is None


def test_an_expired_registration_cannot_be_approved(session: Session) -> None:
    agent_id, _ = pending(session)
    Cached._client().delete(f"registration:{agent_id}")

    assert Agent(session=session, id=agent_id).approve() is None


def test_an_approved_agent_is_claimed_once(session: Session) -> None:
    agent_id, code = pending(session)
    Agent(session=session, id=agent_id).approve()

    agent = Agent(session=session).claimed(code)

    assert agent is not None
    assert agent.id == agent_id
    assert agent.redeem(code)
    assert not agent.redeem(code)
    assert Agent(session=session).claimed(code) is None


def test_a_known_agent_registers_again_under_its_id(session: Session) -> None:
    agent = approved(session)
    again = Agent(session=session, id=agent.id, name="gamma", labels=[])
    code = again.claim_code(False)

    with pytest.raises(InvalidTransitionError):
        Agent(session=session).claimed(code)
    agent.approve()

    assert len(list(session.exec(select(Agent)))) == 1
    claimed = Agent(session=session).claimed(code)
    assert claimed is not None
    assert claimed.id == agent.id
    assert claimed.name == "gamma"
    assert claimed.state is State.ENABLED


def test_self_registering_agents_are_enabled_at_once(session: Session) -> None:
    agent_id, code = pending(session, auto_register=True)

    assert not Cached._client().exists(f"registration:{agent_id}")
    claimed = Agent(session=session).claimed(code)
    assert claimed is not None
    assert claimed.id == agent_id
    assert claimed.state is State.ENABLED


def test_an_unknown_code_claims_nothing(session: Session) -> None:
    assert Agent(session=session).claimed("gamma") is None


def test_the_first_heartbeat_opens_a_lease(session: Session) -> None:
    agent = approved(session)

    lease = agent.heartbeat(2)

    assert agent.lease == lease
    assert lease.capacity == 2
    assert lease.expires_at == lease.heartbeat_at + LEASE_SECONDS
    assert abs(lease.heartbeat_at - time.time()) <= 1


def test_a_live_lease_is_renewed_under_the_same_id(session: Session) -> None:
    agent = approved(session)
    first = agent.heartbeat(1)

    second = agent.heartbeat(3)

    assert second.id == first.id
    assert second.capacity == 3


def test_reading_the_lease_does_not_renew_it(session: Session) -> None:
    agent = approved(session)
    agent.heartbeat(1)
    Cached._client().expire(f"lease:{agent.id}", 5)

    assert agent.lease is not None

    ttl = Cached._client().ttl(f"lease:{agent.id}")
    assert isinstance(ttl, int)
    assert ttl <= 5


def test_a_lapsed_lease_fails_what_it_held_before_a_new_one(session: Session) -> None:
    agent = approved(session)
    first = agent.heartbeat(0)
    held = [
        deploy(session, agent, status)
        for status in (DeploymentStatus.STARTING, DeploymentStatus.READY)
    ]
    waiting = deploy(session, agent, DeploymentStatus.PENDING)
    Cached._client().delete(f"lease:{agent.id}")

    second = agent.heartbeat(0)

    assert second.id != first.id
    for deployment in held:
        session.refresh(deployment)
        assert deployment.status is DeploymentStatus.FAILED
        assert deployment.reason == "lease expired"
    session.refresh(waiting)
    assert waiting.status is DeploymentStatus.PENDING


def test_a_lapsed_lease_closes_the_recording_of_what_it_held(session: Session) -> None:
    agent = approved(session)
    agent.heartbeat(0)
    held = deploy(session, agent, DeploymentStatus.READY)
    link = Link(session=session, deployment_id=held.id, user_id=uuid.uuid4())
    link.save()
    recording = Recording(session=session, deployment_id=held.id, link_id=link.id)
    recording.start(held.console, "alpha v1", None, 1024)
    Cached._client().delete(f"lease:{agent.id}")

    agent.heartbeat(0)

    session.refresh(held)
    assert held.status is DeploymentStatus.FAILED
    assert Recording(session=session).load(id=recording.id).size_bytes > 0


def queued(session: Session, created_at: int, agent: Agent | None = None) -> Deployment:
    pinned = {} if agent is None else {"agent_id": agent.id}
    deployment = Deployment(session=session, created_at=created_at, **pinned, **FIELDS)
    deployment.save()
    return deployment


def test_a_heartbeat_assigns_the_oldest_waiting_within_capacity(session: Session) -> None:
    agent = approved(session)
    newest, oldest, middle = (queued(session, at) for at in (3, 1, 2))

    agent.heartbeat(2)

    for deployment in (oldest, middle):
        session.refresh(deployment)
        assert deployment.agent_id == agent.id
        assert deployment.vm_id is not None
        assert deployment.status is DeploymentStatus.PENDING
    session.refresh(newest)
    assert newest.agent_id is None
    assert newest.vm_id is None


def test_a_pinned_deployment_goes_first_even_when_an_older_one_waits(session: Session) -> None:
    agent = approved(session)
    older = queued(session, 1)
    pinned = queued(session, 2, agent)

    agent.heartbeat(1)

    session.refresh(pinned)
    session.refresh(older)
    assert pinned.vm_id is not None
    assert older.agent_id is None


def test_capacity_zero_assigns_nothing(session: Session) -> None:
    agent = approved(session)
    waiting, pinned = queued(session, 1), queued(session, 2, agent)

    agent.heartbeat(0)

    session.refresh(waiting)
    session.refresh(pinned)
    assert waiting.agent_id is None
    assert pinned.vm_id is None
    assert agent.desired() == []


def test_held_deployments_take_their_slots(session: Session) -> None:
    agent = approved(session)
    agent.heartbeat(1)
    queued(session, 1)
    agent.heartbeat(1)
    waiting = queued(session, 2)

    agent.heartbeat(1)

    session.refresh(waiting)
    assert waiting.agent_id is None
    assert len(agent.deployments) == 1


def test_a_pinned_deployment_is_never_assigned_to_another_agent(session: Session) -> None:
    alpha, beta = approved(session), approved(session, "beta")
    pinned = queued(session, 1, alpha)

    beta.heartbeat(2)

    session.refresh(pinned)
    assert pinned.agent_id == alpha.id
    assert pinned.vm_id is None
    assert not pinned.assign(beta.id)


def test_only_one_of_two_agents_takes_a_deployment(session: Session) -> None:
    alpha, beta = approved(session), approved(session, "beta")
    waiting = queued(session, 1)

    taken = [waiting.assign(alpha.id), waiting.assign(beta.id)]

    assert taken == [True, False]
    session.refresh(waiting)
    assert waiting.agent_id == alpha.id


def test_an_assignment_keeps_its_vm_id(session: Session) -> None:
    agent = approved(session)
    waiting = queued(session, 1, agent)
    assert waiting.assign(agent.id)
    vm_id = waiting.vm_id

    assert waiting.assign(agent.id)
    assert waiting.vm_id == vm_id


def test_a_deployment_no_longer_pending_is_not_assigned(session: Session) -> None:
    agent = approved(session)
    started = deploy(session, agent, DeploymentStatus.STARTING)

    assert not started.assign(agent.id)
