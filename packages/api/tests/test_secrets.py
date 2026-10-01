import dataclasses
import time
from typing import Any

import pytest
from sqlmodel import Session

from jatszoter_api.models import Agent, Credential, Role, State, User
from jatszoter_api.models.secret import REFRESH_BEFORE_SECONDS, SIGNATURE_WINDOW_SECONDS

NOW = 1790000000
KID = "current"
KEYS = {"old": "a" * 64, KID: "b" * 64}
DAY = 24 * 60 * 60
PATH = "/api/v1/agents/alpha/deployments/beta/transition"
BODY = b'{"expected": "BOOTSTRAPPING", "target": "READY"}'


def enabled_agent(session: Session) -> Agent:
    agent = Agent(session=session, name="alpha", state=State.ENABLED)
    agent.save()
    return agent


def signed(owner: Agent | User, credential: Credential) -> bool:
    signature = credential.sign(KEYS, "POST", PATH, BODY, NOW)
    assert signature is not None
    return owner.verify(credential, KEYS, "POST", PATH, BODY, NOW, signature, now=NOW)


def test_a_registered_agent_waits_disabled(session: Session) -> None:
    agent = Agent(session=session, name="alpha")
    agent.save()

    assert agent.state is State.DISABLED
    assert agent.secret.generation == 0


def test_the_first_issue_opens_generation_one(session: Session) -> None:
    agent = enabled_agent(session)

    credential, key = agent.issue(2 * DAY, KID, KEYS)

    assert credential.generation == 1
    assert credential.kid == "current"
    assert credential.key(KEYS) == key
    assert signed(agent, credential)


def test_a_disabled_agent_is_refused(session: Session) -> None:
    agent = enabled_agent(session)
    credential, _ = agent.issue(2 * DAY, KID, KEYS)
    agent.state = State.DISABLED

    assert not signed(agent, credential)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", PATH, BODY),
        ("POST", "/api/v1/agents/alpha/heartbeat", BODY),
        ("POST", PATH, b'{"expected": "BOOTSTRAPPING", "target": "FAILED"}'),
    ],
)
def test_a_signature_does_not_cover_another_request(
    session: Session, method: str, path: str, body: bytes
) -> None:
    agent = enabled_agent(session)
    credential, _ = agent.issue(2 * DAY, KID, KEYS)
    signature = credential.sign(KEYS, "POST", PATH, BODY, NOW)
    assert signature is not None

    assert not agent.verify(credential, KEYS, method, path, body, NOW, signature, now=NOW)


@pytest.mark.parametrize("skew", [SIGNATURE_WINDOW_SECONDS + 1, -SIGNATURE_WINDOW_SECONDS - 1])
def test_a_signature_outside_the_window_is_refused(session: Session, skew: int) -> None:
    agent = enabled_agent(session)
    credential, _ = agent.issue(2 * DAY, KID, KEYS)
    signature = credential.sign(KEYS, "POST", PATH, BODY, NOW)
    assert signature is not None

    assert not agent.verify(credential, KEYS, "POST", PATH, BODY, NOW, signature, now=NOW + skew)


def test_an_expired_credential_is_refused(session: Session) -> None:
    agent = enabled_agent(session)
    credential, _ = agent.issue(-1, KID, KEYS)

    assert credential.expired
    assert not signed(agent, credential)


@pytest.mark.parametrize(
    "change",
    [
        {"expires_at": int(time.time()) + 365 * DAY},
        {"generation": 7},
        {"kid": "old"},
        {"kid": "unknown"},
    ],
)
def test_an_altered_credential_signs_nothing(session: Session, change: dict[str, Any]) -> None:
    agent = enabled_agent(session)
    credential, _ = agent.issue(2 * DAY, KID, KEYS)
    signature = credential.sign(KEYS, "POST", PATH, BODY, NOW)
    assert signature is not None
    altered = dataclasses.replace(credential, **change)

    assert not agent.verify(altered, KEYS, "POST", PATH, BODY, NOW, signature, now=NOW)


def test_another_owners_credential_is_refused(session: Session) -> None:
    alpha, beta = enabled_agent(session), enabled_agent(session)
    credential, _ = alpha.issue(2 * DAY, KID, KEYS)

    assert not signed(beta, credential)


def test_a_refresh_keeps_the_old_generation_until_the_new_one_is_used(session: Session) -> None:
    agent = enabled_agent(session)
    old, _ = agent.issue(2 * DAY, KID, KEYS)
    new, _ = agent.issue(2 * DAY, KID, KEYS)

    assert signed(agent, old)
    assert signed(agent, new)
    assert agent.secret is not None
    assert agent.secret.generation == new.generation
    assert agent.secret.pending_generation is None


def test_every_refresh_hands_out_a_fresh_generation(session: Session) -> None:
    agent = enabled_agent(session)
    agent.issue(2 * DAY, KID, KEYS)
    first, _ = agent.issue(2 * DAY, KID, KEYS)
    second, _ = agent.issue(2 * DAY, KID, KEYS)

    assert not signed(agent, first)
    assert agent.state is State.ENABLED
    assert signed(agent, second)


def test_a_superseded_generation_in_use_disables_the_owner(session: Session) -> None:
    agent = enabled_agent(session)
    stolen, _ = agent.issue(2 * DAY, KID, KEYS)
    fresh, _ = agent.issue(2 * DAY, KID, KEYS)
    assert signed(agent, fresh)

    assert not signed(agent, stolen)
    assert agent.state is State.DISABLED
    assert not signed(agent, fresh)


def test_a_credential_is_due_for_refresh_a_day_before_it_expires(session: Session) -> None:
    agent = enabled_agent(session)

    early, _ = agent.issue(REFRESH_BEFORE_SECONDS + 3600, KID, KEYS)
    late, _ = agent.issue(REFRESH_BEFORE_SECONDS, KID, KEYS)

    assert not early.rotation_due
    assert late.rotation_due


def test_credentials_of_a_previous_master_key_keep_working(session: Session) -> None:
    agent = enabled_agent(session)
    credential, _ = agent.issue(2 * DAY, "old", KEYS)

    assert credential.kid == "old"
    assert signed(agent, credential)


def test_a_user_owns_secrets_the_same_way(session: Session) -> None:
    user = User(session=session, name="beta", role=Role.OPERATOR)
    user.save()
    credential, _ = user.issue(3600, KID, KEYS)

    assert user.state is State.DISABLED
    assert not signed(user, credential)
    user.state = State.ENABLED
    assert signed(user, credential)


def test_logout_ends_every_credential_issued_so_far(session: Session) -> None:
    user = User(session=session, name="beta", role=Role.OPERATOR, state=State.ENABLED)
    user.save()
    live, _ = user.issue(3600, KID, KEYS)
    pending, _ = user.issue(3600, KID, KEYS)

    user.logout()

    assert user.secret.generation == pending.generation + 1
    assert user.secret.pending_generation is None
    fresh, _ = user.issue(3600, KID, KEYS)
    assert signed(user, fresh)
    assert not signed(user, live)


def test_a_credential_has_no_key_without_its_master_key(session: Session) -> None:
    agent = enabled_agent(session)
    credential, _ = agent.issue(2 * DAY, KID, KEYS)

    assert credential.key({"old": KEYS["old"]}) is None
    assert credential.sign({}, "POST", PATH, BODY, NOW) is None
    assert not agent.verify(credential, {}, "POST", PATH, BODY, NOW, "alpha", now=NOW)


def test_issuing_under_a_kid_without_a_master_key_fails(session: Session) -> None:
    agent = enabled_agent(session)

    with pytest.raises(RuntimeError, match="no master key"):
        agent.issue(2 * DAY, "unknown", KEYS)
