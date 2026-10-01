import stat
from pathlib import Path

import httpx
import pytest

from jatszoter_api.file import DISK_BASE, File
from jatszoter_api.settings import get_settings

FILER = "http://filer.example.com:8888/buckets/"
BAD_PATHS = [
    "",
    "/alpha",
    "alpha/",
    "alpha//beta",
    "./alpha",
    "alpha/.",
    "../alpha",
    "alpha/../beta",
]


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def filer(monkeypatch: pytest.MonkeyPatch, status: int = 200) -> list[httpx.Request]:
    """Point files at the filer URL and answer its requests here; returns what it got."""
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, content=b"alpha" if request.method == "GET" else b"")

    client = httpx.Client
    monkeypatch.setenv("JATSZOTER_SEAWEED_URL", FILER.rstrip("/"))
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "Client", lambda **_: client(transport=httpx.MockTransport(handle)))
    return requests


def test_a_local_file_round_trips_private(tmp_path: Path) -> None:
    file = File("alpha/beta gamma.cast", b"delta")

    file.put()

    root = tmp_path / "data" / "storage"
    target = root / "alpha" / "beta gamma.cast"
    assert file.url == f"{DISK_BASE}alpha/beta%20gamma.cast"
    assert target.read_bytes() == b"delta"
    assert mode(root) == mode(root / "alpha") == 0o700
    assert mode(target) == 0o600
    stored = File("alpha/beta gamma.cast")
    stored.get()
    assert stored.data == b"delta"

    file.delete()

    assert not target.exists()
    with pytest.raises(httpx.HTTPStatusError):
        stored.get()


def test_a_local_put_replaces_the_file(tmp_path: Path) -> None:
    File("alpha.cast", b"beta").put()

    File("alpha.cast", b"gamma").put()

    stored = File("alpha.cast")
    stored.get()
    assert stored.data == b"gamma"
    assert [path.name for path in (tmp_path / "data" / "storage").iterdir()] == ["alpha.cast"]


def test_without_a_filer_files_go_to_the_data_dir() -> None:
    assert File("alpha.cast").url == f"{DISK_BASE}alpha.cast"


def test_the_filer_gets_put_get_and_delete_on_the_file_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = filer(monkeypatch)
    file = File("alpha/beta.cast", b"gamma")

    file.put()
    file.get()
    file.delete()

    assert file.url == f"{FILER}alpha/beta.cast"
    assert file.data == b"alpha"
    assert [(request.method, str(request.url)) for request in requests] == [
        ("PUT", file.url),
        ("GET", file.url),
        ("DELETE", file.url),
    ]
    assert requests[0].content == b"gamma"


@pytest.mark.parametrize("path", BAD_PATHS)
def test_a_path_that_is_not_plain_and_relative_is_refused(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="not a relative file path"):
        File(path, b"alpha")

    requests = filer(monkeypatch)
    with pytest.raises(ValueError, match="not a relative file path"):
        File(path, b"alpha")
    assert requests == []


@pytest.mark.parametrize("status", [301, 404, 500])
def test_a_filer_answer_other_than_2xx_raises(status: int, monkeypatch: pytest.MonkeyPatch) -> None:
    filer(monkeypatch, status)
    file = File("alpha.cast", b"beta")

    with pytest.raises(httpx.HTTPStatusError):
        file.put()
    with pytest.raises(httpx.HTTPStatusError):
        file.get()
