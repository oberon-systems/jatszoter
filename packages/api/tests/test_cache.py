import json
import pickle
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session

from jatszoter_api.models import Agent, Link, Role, State, User, _cache
from jatszoter_api.models._cache import Cached, _derived, _mac

DAY = 24 * 60 * 60
KID = "current"
KEYS = {KID: "b" * 64}


class Touches:
    """Creates `path` when unpickled, standing in for any code a hostile entry would run."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __reduce__(self) -> tuple[Any, tuple[Path]]:
        return Path.touch, (self.path,)


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


def raw_entry(key: str) -> bytes:
    raw = Cached._client().get(key)
    assert isinstance(raw, bytes)
    return raw


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


def test_a_hit_keeps_the_ttl_so_a_stale_entry_ends_on_time(session: Session) -> None:
    user = viewer(session)
    user._set("alpha", 1, 60)
    Cached._client().expire(f"alpha:{user.id}", 5)

    for _ in range(3):
        assert user._get("alpha", ttl=60) == 1

    assert 0 < ttl(f"alpha:{user.id}") <= 5


def test_a_counter_gets_its_window_with_the_first_count() -> None:
    cache = Cached()

    assert cache._increment("alpha", 60) == 1
    assert cache._increment("alpha", 60) == 2
    assert 0 < ttl("alpha") <= 60


def test_a_counter_whose_expire_fails_is_not_left_without_a_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = type(Cached._client().pipeline())

    def broken(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("alpha")

    monkeypatch.setattr(pipeline, "expire", broken)
    with pytest.raises(ConnectionError):
        Cached()._increment("alpha", 60)

    assert not Cached._client().exists("alpha") or ttl("alpha") > 0


def test_a_counter_taken_back_to_zero_is_dropped() -> None:
    cache = Cached()
    cache._increment("alpha", 60)
    cache._increment("alpha", 60)

    cache._decrement("alpha")
    assert 0 < ttl("alpha") <= 60
    cache._decrement("alpha")
    assert not Cached._client().exists("alpha")
    cache._decrement("alpha")
    assert not Cached._client().exists("alpha")


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


def test_a_pickle_written_by_another_client_never_runs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    marker = tmp_path / "ran"
    Cached._client().set("claim:alpha", pickle.dumps(Touches(marker)))

    assert Cached()._get("claim:alpha") is None

    assert not marker.exists()
    assert "refused a cache entry" in caplog.text
    assert "alpha" not in caplog.text


@pytest.mark.parametrize(
    "raw",
    [
        b'"00000000-0000-4000-8000-000000000001"',
        b"current\n" + b"0" * 64 + b'\n"alpha"',
        b"unknown\n" + b"0" * 64 + b'\n"alpha"',
        b"\n\n",
        b"\xff\xfe",
    ],
)
def test_a_value_written_by_another_client_is_refused(raw: bytes) -> None:
    Cached.connect(None, KEYS, KID)
    Cached._client().set("claim:alpha", raw)

    assert Cached()._get("claim:alpha") is None


def test_a_value_signed_with_a_key_the_api_does_not_hold_is_refused() -> None:
    Cached.connect(None, KEYS, KID)
    payload = json.dumps("alpha").encode()
    mac = _mac(_derived("c" * 64), "claim:alpha", payload)
    Cached._client().set("claim:alpha", b"\n".join((KID.encode(), mac, payload)))

    assert Cached()._get("claim:alpha") is None


def test_a_value_moved_to_another_entry_is_refused() -> None:
    Cached.connect(None, KEYS, KID)
    Cached()._set("claim:alpha", "gamma", 60)
    Cached._client().rename("claim:alpha", "claim:beta")

    assert Cached()._get("claim:beta") is None


def test_a_miss_on_a_refused_owner_entry_loads_the_real_value(session: Session) -> None:
    agent = enabled_agent(session)
    agent.issue(2 * DAY, KID, KEYS)
    Cached._client().set(f"secret:{agent.id}", json.dumps({"generation": 99}))

    assert agent.secret.generation == 1


def test_the_master_key_is_not_the_cache_key() -> None:
    Cached.connect(None, KEYS, KID)
    Cached()._set("alpha", 1, 60)

    raw = raw_entry("alpha")
    assert KEYS[KID].encode() not in raw
    assert _mac(KEYS[KID].encode(), "alpha", b"1") not in raw
    assert _mac(_derived(KEYS[KID]), "alpha", b"1") in raw


def test_an_entry_of_a_retired_key_stays_readable_while_that_key_is_listed() -> None:
    Cached.connect(None, {"old": "a" * 64}, "old")
    Cached()._set("alpha", 1, 60)
    raw = raw_entry("alpha")

    Cached.connect(None, {"old": "a" * 64, KID: "b" * 64}, KID)
    Cached._client().set("alpha", raw)

    assert Cached()._get("alpha") == 1


def test_an_entry_of_a_dropped_key_is_refused() -> None:
    Cached.connect(None, {"old": "a" * 64}, "old")
    Cached()._set("alpha", 1, 60)
    raw = raw_entry("alpha")

    Cached.connect(None, KEYS, KID)
    Cached._client().set("alpha", raw)

    assert Cached()._get("alpha") is None


def test_without_a_master_key_the_cache_still_works_on_a_key_of_its_own() -> None:
    Cached.connect(None)

    Cached()._set("alpha", {"beta": 1}, 60)

    assert Cached()._get("alpha") == {"beta": 1}


def test_a_lease_survives_the_cache_with_its_types(session: Session) -> None:
    agent = enabled_agent(session)

    lease = agent.heartbeat(2)

    stored = agent.lease
    assert stored == lease
    assert stored is not None
    assert stored.id == lease.id


def test_a_cached_secret_reads_the_same_on_a_miss_and_on_a_hit(session: Session) -> None:
    agent = enabled_agent(session)
    agent.issue(2 * DAY, KID, KEYS)

    missed = agent.secret
    hit = agent.secret

    assert missed == hit
    assert (hit.generation, hit.pending_generation) == (1, None)


def test_a_link_session_resumes_through_the_cache(session: Session) -> None:
    link = Link(session=session, deployment_id=uuid.uuid4(), user_id=uuid.uuid4())
    link.save()

    token = link.open()

    assert Link(session=session).resume(token).id == link.id
    with pytest.raises(IndexError):
        Link(session=session).resume("alpha")


def test_connecting_without_a_dsn_opens_an_empty_in_memory_cache() -> None:
    Cached()._set("alpha", 1, 60)

    Cached.connect(None)

    assert Cached()._get("alpha") is None
    assert Cached._client().dbsize() == 0


def test_an_unconnected_cache_refuses_to_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_cache, "_CLIENT", None)

    with pytest.raises(RuntimeError, match="not connected"):
        Cached()._get("alpha")
