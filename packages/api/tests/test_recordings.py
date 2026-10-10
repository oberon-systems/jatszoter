import json
import uuid
from typing import Any

import httpx
import pytest
from sqlmodel import Session

from jatszoter_api.errors import ConflictError
from jatszoter_api.file import File
from jatszoter_api.models import Console, Recording
from jatszoter_api.models._cache import Cached
from jatszoter_api.models.recording import FULL, live

TAIL = 1024
CONSOLE_LIMIT = 1024 * 1024
LIMIT = 1024 * 1024

Event = list[Any]


def ship(console: Console, data: bytes) -> tuple[int, bytes]:
    return console.append(console.held(None), data, None, TAIL, CONSOLE_LIMIT)


def ready(boot: bytes = b"boot\r\n") -> Console:
    """A console that reached READY after printing `boot`."""
    console = Console(uuid.uuid4())
    ship(console, boot)
    console.mark_ready(None)
    return console


def record(session: Session, console: Console, limit: int = LIMIT) -> Recording:
    recording = Recording(
        session=session, deployment_id=console.deployment_id, link_id=uuid.uuid4()
    )
    recording.start(console, "alpha v1", None, limit)
    return recording


def feed(console: Console, recording: Recording, data: bytes, limit: int = LIMIT) -> None:
    start, new = ship(console, data)
    recording.output(start, new, console, None, limit)


def raw(recording: Recording) -> bytes:
    stored = File(recording.path)
    stored.get()
    return stored.data


def cast(recording: Recording) -> tuple[dict[str, Any], list[Event]]:
    lines = raw(recording).decode().splitlines()
    return json.loads(lines[0]), [json.loads(line) for line in lines[1:]]


def shown(events: list[Event]) -> str:
    return "".join(text for _, code, text in events if code == "o")


def test_the_header_is_the_first_line(session: Session) -> None:
    recording = record(session, ready())

    header, events = cast(recording)

    assert header == {
        "version": 2,
        "width": 80,
        "height": 24,
        "timestamp": recording.created_at,
        "title": "alpha v1",
    }
    assert events == []
    assert recording.url.endswith(f"recordings/{recording.deployment_id}/{recording.id}.cast")
    assert live(recording.deployment_id) == recording.id


def test_output_after_ready_becomes_events(session: Session) -> None:
    console = ready()
    recording = record(session, console)

    feed(console, recording, b"alpha ")
    feed(console, recording, b"beta")

    _, events = cast(recording)
    assert [code for _, code, _ in events] == ["o", "o"]
    assert shown(events) == "alpha beta"


def test_a_link_opened_after_ready_starts_with_the_bytes_since_ready(session: Session) -> None:
    console = ready()
    ship(console, b"login: ")

    recording = record(session, console)
    feed(console, recording, b"alpha")

    _, events = cast(recording)
    assert events[0] == [0.0, "o", "login: "]
    assert shown(events) == "login: alpha"


def test_a_tail_that_lost_the_ready_offset_starts_at_the_log_end(session: Session) -> None:
    console = ready()
    ship(console, b"x" * (TAIL + 16))

    recording = record(session, console)
    feed(console, recording, b"alpha")

    _, events = cast(recording)
    assert shown(events) == "alpha"


def test_a_resize_and_a_marker_sit_in_order_with_the_output(session: Session) -> None:
    console = ready()
    recording = record(session, console)

    feed(console, recording, b"alpha")
    recording.resize(100, 30, None, LIMIT)
    recording.mark("control operator", None, LIMIT)
    feed(console, recording, b"beta")

    _, events = cast(recording)
    assert [(code, text) for _, code, text in events] == [
        ("o", "alpha"),
        ("r", "100x30"),
        ("m", "control operator"),
        ("o", "beta"),
    ]
    times = [at for at, _, _ in events]
    assert times == sorted(times)
    assert all(at >= 0 for at in times)


def test_a_character_split_over_two_frames_comes_out_whole(session: Session) -> None:
    console = ready()
    recording = record(session, console)
    whole = "\u00e9".encode()

    feed(console, recording, b"caf" + whole[:1])
    feed(console, recording, whole[1:] + b"!")

    _, events = cast(recording)
    assert [text for _, _, text in events] == ["caf", "\u00e9!"]


def test_close_stores_the_size_and_a_duration(session: Session) -> None:
    console = ready()
    recording = record(session, console)
    feed(console, recording, b"alpha")
    recording.created_at -= 5

    recording.close(None)
    session.expunge_all()

    stored = Recording(session=session).load(id=recording.id)
    assert stored.size_bytes == len(raw(recording))
    assert stored.duration_seconds >= 5
    assert stored.deployment_id == console.deployment_id


def test_output_resumes_after_the_cache_is_lost(session: Session) -> None:
    console = ready()
    recording = record(session, console)
    feed(console, recording, b"alpha")
    Cached.connect(None)

    assert live(recording.deployment_id) is None
    recording.resume(console, None)
    feed(console, recording, b"beta")

    _, events = cast(recording)
    assert live(recording.deployment_id) == recording.id
    assert shown(events) == "alphabeta"


def test_output_before_ready_writes_nothing(session: Session) -> None:
    console = Console(uuid.uuid4())
    recording = record(session, console)

    feed(console, recording, b"boot\r\n")
    console.mark_ready(None)
    feed(console, recording, b"alpha")

    _, events = cast(recording)
    assert shown(events) == "alpha"


def test_an_overlapping_frame_is_not_written_twice(session: Session) -> None:
    console = ready()
    recording = record(session, console)
    start, new = ship(console, b"alpha")
    recording.output(start, new, console, None, LIMIT)

    recording.output(start, new, console, None, LIMIT)
    recording.output(start + 2, new[2:] + b"beta", console, None, LIMIT)

    _, events = cast(recording)
    assert shown(events) == "alphabeta"


def test_an_invalid_byte_becomes_the_replacement_character(session: Session) -> None:
    console = ready()
    recording = record(session, console)

    feed(console, recording, b"a\xffb")

    _, events = cast(recording)
    assert shown(events) == "a\ufffdb"


def test_past_the_limit_the_marker_appears_once_and_the_file_stops(session: Session) -> None:
    console = ready()
    recording = record(session, console)
    feed(console, recording, b"alpha")
    limit = len(raw(recording)) + 8

    feed(console, recording, b"beta" * 8, limit)
    full = len(raw(recording))
    feed(console, recording, b"gamma", limit)
    recording.mark("control operator", None, limit)
    recording.resize(100, 30, None, limit)

    _, events = cast(recording)
    assert [text for _, code, text in events if code == "m"] == [FULL]
    assert shown(events) == "alpha"
    assert len(raw(recording)) == full


def test_a_failed_append_is_taken_again_with_the_next_frame(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    console = ready()
    recording = record(session, console)

    def refuse(self: File) -> None:
        raise httpx.ConnectError("refused")

    start, new = ship(console, b"alpha")
    with monkeypatch.context() as patched:
        patched.setattr(File, "append", refuse)
        with pytest.raises(httpx.ConnectError):
            recording.output(start, new, console, None, LIMIT)
    feed(console, recording, b"beta")

    _, events = cast(recording)
    assert shown(events) == "alphabeta"


def test_a_closed_recording_is_no_longer_live_and_closes_once(session: Session) -> None:
    console = ready()
    recording = record(session, console)

    recording.close(None)
    size, duration = recording.size_bytes, recording.duration_seconds
    recording.created_at -= 60
    recording.close(None)

    assert live(recording.deployment_id) is None
    assert (recording.size_bytes, recording.duration_seconds) == (size, duration)
    assert size > 0


def test_a_second_recording_of_one_link_is_refused(session: Session) -> None:
    console = ready()
    first = record(session, console)
    second = Recording(session=session, deployment_id=console.deployment_id, link_id=first.link_id)

    with pytest.raises(ConflictError):
        second.start(console, "alpha v1", None, LIMIT)

    assert live(console.deployment_id) == first.id
