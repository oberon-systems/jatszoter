import time
import uuid
from typing import Any

import pytest
from sqlmodel import Session, select

from jatszoter_api.errors import InvalidTransitionError
from jatszoter_api.lifecycle import DeploymentStatus
from jatszoter_api.models import Agent, Deployment, State
from jatszoter_api.models._cache import Cached
from jatszoter_api.models.agent import LEASE_SECONDS
from jatszoter_api.settings import get_settings

FIELDS: dict[str, Any] = {
    "scenario_id": uuid.uuid4(),
    "cpus": 1,
    "memory_mib": 512,
    "disk_gib": 4,
    "bootstrap_seconds": 600,
    "check_seconds": 30,
    "session_minutes": 60,
}


def pending(session: Session, name: str = "alpha") -> tuple[uuid.UUID, str]:
    agent = Agent(session=session, name=name, labels=["beta"])
    return agent.id, agent.claim_code()


def approved(session: Session, name: str = "alpha") -> Agent:
    agent_id, _ = pending(session, name)
    agent = Agent.approve(session, agent_id)
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
        Agent.claimed(session, code)


def test_approval_stores_the_agent_enabled(session: Session) -> None:
    agent_id, _ = pending(session)

    agent = Agent.approve(session, agent_id)

    assert agent is not None
    assert agent.id == agent_id
    assert agent.name == "alpha"
    assert agent.labels == ["beta"]
    assert agent.state is State.ENABLED
    assert not Cached._client().exists(f"registration:{agent_id}")


def test_a_registration_is_approved_once(session: Session) -> None:
    agent_id, _ = pending(session)
    Agent.approve(session, agent_id)

    assert Agent.approve(session, agent_id) is None


def test_an_expired_registration_cannot_be_approved(session: Session) -> None:
    agent_id, _ = pending(session)
    Cached._client().delete(f"registration:{agent_id}")

    assert Agent.approve(session, agent_id) is None


def test_an_approved_agent_is_claimed_once(session: Session) -> None:
    agent_id, code = pending(session)
    Agent.approve(session, agent_id)

    agent = Agent.claimed(session, code)

    assert agent is not None
    assert agent.id == agent_id
    assert agent.redeem(code)
    assert not agent.redeem(code)
    assert Agent.claimed(session, code) is None


def test_a_known_agent_registers_again_under_its_id(session: Session) -> None:
    agent = approved(session)
    again = Agent(session=session, name="gamma", labels=[])
    again.id = agent.id
    code = again.claim_code()

    with pytest.raises(InvalidTransitionError):
        Agent.claimed(session, code)
    Agent.approve(session, agent.id)

    assert len(list(session.exec(select(Agent)))) == 1
    claimed = Agent.claimed(session, code)
    assert claimed is not None
    assert claimed.id == agent.id
    assert claimed.name == "gamma"
    assert claimed.state is State.ENABLED


def test_self_registering_agents_are_enabled_at_once(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JATSZOTER_AUTO_REGISTER_AGENTS", "true")
    get_settings.cache_clear()

    agent_id, code = pending(session)

    assert not Cached._client().exists(f"registration:{agent_id}")
    claimed = Agent.claimed(session, code)
    assert claimed is not None
    assert claimed.id == agent_id
    assert claimed.state is State.ENABLED


def test_an_unknown_code_claims_nothing(session: Session) -> None:
    assert Agent.claimed(session, "gamma") is None


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
