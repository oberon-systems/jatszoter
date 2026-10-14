import uuid

import httpx
import pytest
from sqlmodel import Session

from jatszoter_api.errors import TooLargeError
from jatszoter_api.file import File
from jatszoter_api.lifecycle import Actor, DeploymentStatus
from jatszoter_api.models import Console, Deployment
from jatszoter_api.models._cache import Cached
from jatszoter_api.models.console import Holder

S = DeploymentStatus
AGENT = uuid.uuid4()
TAIL = 8
LIMIT = 64


def write(console: Console, offset: int, data: bytes, limit: int = LIMIT) -> tuple[int, bytes]:
    return console.append(offset, data, None, TAIL, limit)


def log(console: Console) -> bytes:
    stored = File(console.path)
    stored.get()
    return stored.data


def test_appends_in_order_land_in_the_log_and_the_tail() -> None:
    console = Console(uuid.uuid4())

    assert write(console, 0, b"alpha") == (0, b"alpha")
    assert write(console, 5, b"bet") == (5, b"bet")

    assert log(console) == b"alphabet"
    assert console.tail() == (0, b"alphabet")
    assert console.held(None) == 8


def test_a_repeated_frame_adds_only_its_new_part() -> None:
    console = Console(uuid.uuid4())
    write(console, 0, b"alpha")

    assert write(console, 2, b"phabeta") == (5, b"beta")
    assert write(console, 0, b"alphabeta") == (9, b"")

    assert log(console) == b"alphabeta"


def test_the_tail_keeps_the_last_bytes_and_its_start_follows() -> None:
    console = Console(uuid.uuid4())

    write(console, 0, b"alphabeta")
    assert console.tail() == (1, b"lphabeta")

    write(console, 9, b"gamma")
    assert console.tail() == (6, b"etagamma")

    write(console, 14, b"0123456789")
    assert console.tail() == (16, b"23456789")
    assert log(console) == b"alphabetagamma0123456789"


def test_since_inside_the_tail_returns_the_rest() -> None:
    console = Console(uuid.uuid4())
    write(console, 0, b"alphabetagamma")

    assert console.since(10) == b"amma"
    assert console.since(14) == b""


def test_a_lost_cache_costs_only_the_tail() -> None:
    console = Console(uuid.uuid4())
    write(console, 0, b"alpha")

    Cached.connect(None)

    assert console.held(None) == 5
    assert console.tail() == (5, b"")
    assert write(console, 5, b"beta") == (5, b"beta")
    assert console.since(5) == b"beta"
    assert log(console) == b"alphabeta"


def test_mark_ready_records_the_end() -> None:
    console = Console(uuid.uuid4())
    write(console, 0, b"alpha")
    assert console.ready() is None

    console.mark_ready(None)
    write(console, 5, b"beta")

    assert console.ready() == 5


def test_the_holder_is_the_trainee_until_given_survives_an_append_and_goes_with_the_drop() -> None:
    console = Console(uuid.uuid4())
    key = f"console:{console.deployment_id}:control"
    assert console.control() is Holder.TRAINEE

    console.give(Holder.OPERATOR)
    Cached._client().expire(key, 5)
    write(console, 0, b"alpha")

    left = Cached._client().ttl(key)
    assert console.control() is Holder.OPERATOR
    assert isinstance(left, int) and left > 5

    console.drop()
    assert console.control() is Holder.TRAINEE


def test_a_deployment_marks_ready_and_drops_the_tail_when_it_ends(session: Session) -> None:
    deployment = Deployment(
        session=session,
        status=S.BOOTSTRAPPING,
        agent_id=AGENT,
        scenario_id=uuid.uuid4(),
        image_id=uuid.uuid4(),
        trainee_id=uuid.uuid4(),
        cpus=1,
        memory_mib=512,
        disk_gib=4,
        bootstrap_seconds=600,
        check_seconds=30,
        session_minutes=60,
    )
    deployment.save()
    console = deployment.console
    write(console, 0, b"alpha")

    deployment.transition("bootstrap", S.BOOTSTRAPPING, actor=Actor.AGENT, agent_id=AGENT)
    write(console, 5, b"beta")
    assert console.ready() == 5

    deployment.transition("stop", S.READY, actor=Actor.OPERATOR)
    deployment.transition("destroy", S.STOPPING, actor=Actor.AGENT, agent_id=AGENT)

    assert console.ready() is None
    assert console.since(0) is None
    assert log(console) == b"alphabeta"


def test_an_offset_past_what_is_held_writes_nothing() -> None:
    console = Console(uuid.uuid4())
    write(console, 0, b"alpha")

    assert write(console, 9, b"beta") == (5, b"")

    assert log(console) == b"alpha"
    assert console.tail() == (0, b"alpha")


def test_a_frame_over_the_limit_is_cut_and_the_next_one_refused() -> None:
    console = Console(uuid.uuid4())
    write(console, 0, b"alpha", limit=8)

    assert write(console, 5, b"betagamma", limit=8) == (5, b"bet")
    with pytest.raises(TooLargeError) as refused:
        write(console, 8, b"agamma", limit=8)

    assert "gamma" not in str(refused.value)
    assert log(console) == b"alphabet"


def test_since_older_than_the_tail_is_none() -> None:
    console = Console(uuid.uuid4())
    assert console.since(0) is None

    write(console, 0, b"alphabeta")

    assert console.since(0) is None


def test_a_failing_log_append_leaves_the_tail_and_end(monkeypatch: pytest.MonkeyPatch) -> None:
    console = Console(uuid.uuid4())
    write(console, 0, b"alpha")
    client = httpx.Client
    failing = httpx.MockTransport(lambda _: httpx.Response(500))
    monkeypatch.setattr(httpx, "Client", lambda **_: client(transport=failing))

    with pytest.raises(httpx.HTTPStatusError):
        console.append(5, b"beta", "http://filer.example.com:8888/", TAIL, LIMIT)

    assert console.held(None) == 5
    assert console.tail() == (0, b"alpha")
