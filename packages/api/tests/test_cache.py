import uuid

import pytest
from sqlmodel import Session

from jatszoter_api.models import Agent, Role, State, User
from jatszoter_api.models._cache import cache_client, cached

DAY = 24 * 60 * 60


class Box:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.loads = 0

    @cached()
    def value(self) -> int:
        self.loads += 1
        return 42

    @cached(key=lambda box: f"box:{box.id}", ttl=60)
    def named(self) -> str:
        return "alpha"


def ttl(key: str) -> int:
    seconds = cache_client().ttl(key)
    assert isinstance(seconds, int)
    return seconds


def enabled_agent(session: Session) -> Agent:
    agent = Agent(session=session, name="alpha", state=State.ENABLED)
    agent.save()
    return agent


def test_nothing_is_cached_unless_asked(session: Session) -> None:
    user = User(session=session, name="alpha", role=Role.VIEWER)
    user.save()

    user.load(id=user.id)

    assert cache_client().dbsize() == 0


def test_a_property_is_served_from_the_cache() -> None:
    box = Box()

    assert box.value == box.value == 42
    assert box.loads == 1


def test_the_default_key_is_the_id_and_the_default_ttl_ten_seconds() -> None:
    box = Box()
    assert box.value

    assert 0 < ttl(str(box.id)) <= 10


def test_the_key_and_ttl_are_parameters() -> None:
    box = Box()
    assert box.named == "alpha"

    assert 50 < ttl(f"box:{box.id}") <= 60


def test_a_hit_extends_the_ttl() -> None:
    box = Box()
    assert box.value
    cache_client().expire(str(box.id), 1)

    assert box.value

    assert ttl(str(box.id)) > 5


def test_del_drops_the_entry() -> None:
    box = Box()
    assert box.value

    del box.value

    assert not cache_client().exists(str(box.id))
    assert box.value == 42
    assert box.loads == 2


def test_an_owner_secret_is_cached_under_its_owner(session: Session) -> None:
    agent = enabled_agent(session)
    agent.issue(2 * DAY)

    assert agent.secret is not None
    assert cache_client().exists(f"secret:{agent.id}")


@pytest.mark.parametrize("refreshes", [1, 2])
def test_a_refresh_drops_the_cached_secret(session: Session, refreshes: int) -> None:
    agent = enabled_agent(session)
    agent.issue(2 * DAY)
    assert agent.secret is not None

    for _ in range(refreshes):
        agent.issue(2 * DAY)

    assert not cache_client().exists(f"secret:{agent.id}")
    assert agent.secret is not None
    assert agent.secret.pending_generation == 1 + refreshes
