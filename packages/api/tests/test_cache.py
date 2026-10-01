import pytest
from sqlmodel import Session

from jatszoter_api.models import Agent, Role, State, User, _cache
from jatszoter_api.models._cache import Cached

DAY = 24 * 60 * 60
KID = "current"
KEYS = {KID: "b" * 64}


def ttl(key: str) -> int:
    seconds = Cached._client().ttl(key)
    assert isinstance(seconds, int)
    return seconds


def viewer(session: Session) -> User:
    user = User(session=session, name="alpha", role=Role.VIEWER)
    user.save()
    return user


def enabled_agent(session: Session) -> Agent:
    agent = Agent(session=session, name="alpha", state=State.ENABLED)
    agent.save()
    return agent


def test_nothing_is_cached_unless_asked(session: Session) -> None:
    user = viewer(session)

    User(session=session).load(id=user.id)

    assert Cached._client().dbsize() == 0


def test_cached_keeps_a_value_under_its_key_for_its_ttl() -> None:
    cache = Cached()

    cache._set("alpha", {"beta": 1}, 60)

    assert cache._get("alpha") == {"beta": 1}
    assert 50 < ttl("alpha") <= 60


def test_cached_misses_with_none() -> None:
    assert Cached()._get("gamma") is None


def test_cached_extends_and_deletes_an_entry() -> None:
    cache = Cached()
    cache._set("alpha", 1, 60)

    assert cache._ttl("alpha", 120)
    assert ttl("alpha") > 110
    assert cache._delete("alpha")
    assert not cache._delete("alpha")
    assert not cache._ttl("alpha", 60)


def test_a_miss_loads_once_under_the_owner_for_ten_seconds(session: Session) -> None:
    user = viewer(session)
    loads: list[int] = []

    def load() -> int:
        loads.append(1)
        return 42

    assert user._get("alpha", load) == user._get("alpha", load) == 42
    assert len(loads) == 1
    assert 0 < ttl(f"alpha:{user.id}") <= 10


def test_a_miss_without_a_loader_stores_nothing(session: Session) -> None:
    user = viewer(session)

    assert user._get("alpha") is None
    assert not Cached._client().exists(f"alpha:{user.id}")


def test_a_hit_extends_the_ttl(session: Session) -> None:
    user = viewer(session)
    user._set("alpha", 1, 60)
    Cached._client().expire(f"alpha:{user.id}", 1)

    assert user._get("alpha", ttl=60) == 1

    assert ttl(f"alpha:{user.id}") > 50


def test_delete_drops_the_owner_entry(session: Session) -> None:
    user = viewer(session)
    user._set("alpha", 1)

    assert user._delete("alpha")
    assert not Cached._client().exists(f"alpha:{user.id}")


def test_an_owner_secret_is_cached_under_its_owner(session: Session) -> None:
    agent = enabled_agent(session)
    agent.issue(2 * DAY, KID, KEYS)

    assert agent.secret is not None
    assert Cached._client().exists(f"secret:{agent.id}")


@pytest.mark.parametrize("refreshes", [1, 2])
def test_a_refresh_drops_the_cached_secret(session: Session, refreshes: int) -> None:
    agent = enabled_agent(session)
    agent.issue(2 * DAY, KID, KEYS)
    assert agent.secret is not None

    for _ in range(refreshes):
        agent.issue(2 * DAY, KID, KEYS)

    assert not Cached._client().exists(f"secret:{agent.id}")
    assert agent.secret is not None
    assert agent.secret.pending_generation == 1 + refreshes


def test_connecting_without_a_dsn_opens_an_empty_in_memory_cache() -> None:
    Cached()._set("alpha", 1, 60)

    Cached.connect(None)

    assert Cached()._get("alpha") is None
    assert Cached._client().dbsize() == 0


def test_an_unconnected_cache_refuses_to_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_cache, "_CLIENT", None)

    with pytest.raises(RuntimeError, match="not connected"):
        Cached()._get("alpha")
