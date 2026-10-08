import stat
from pathlib import Path

import httpx
import pytest

from jatszoter_api.file import DISK_BASE, DiskFiler, File

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
    """Answer the filer's requests here; returns what it got."""
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "HEAD":
            return httpx.Response(status, headers={"Content-Length": "5"})
        return httpx.Response(status, content=b"alpha" if request.method == "GET" else b"")

    client = httpx.Client
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


def test_local_appends_create_a_private_file_and_keep_every_body_in_order(
    tmp_path: Path,
) -> None:
    assert File("alpha/beta.log").size() == 0

    File("alpha/beta.log", b"gamma").append()
    assert File("alpha/beta.log").size() == 5

    File("alpha/beta.log", b"delta").append()
    assert File("alpha/beta.log").size() == 10

    root = tmp_path / "data" / "storage"
    target = root / "alpha" / "beta.log"
    assert target.read_bytes() == b"gammadelta"
    assert mode(root) == mode(root / "alpha") == 0o700
    assert mode(target) == 0o600


def test_a_local_append_adds_to_what_was_put() -> None:
    File("alpha.log", b"beta").put()
    assert File("alpha.log").size() == 4

    File("alpha.log", b"gamma").append()

    stored = File("alpha.log")
    stored.get()
    assert stored.data == b"betagamma"
    assert stored.size() == 9


def test_a_local_append_under_a_file_fails_and_changes_nothing(tmp_path: Path) -> None:
    File("alpha", b"beta").put()

    with pytest.raises(OSError):
        File("alpha/gamma.log", b"delta").append()

    assert (tmp_path / "data" / "storage" / "alpha").read_bytes() == b"beta"


def test_a_local_append_through_dot_dot_is_refused_before_the_disk(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a relative file path"):
        File("alpha/../beta.log", b"gamma").append()

    assert not (tmp_path / "data").exists()


def test_the_disk_filer_answers_a_plain_post_with_405(tmp_path: Path) -> None:
    client = httpx.Client(transport=DiskFiler(tmp_path))

    assert client.post(f"{DISK_BASE}alpha.log", content=b"beta").status_code == 405
    assert not (tmp_path / "alpha.log").exists()


def test_without_a_filer_files_go_to_the_data_dir() -> None:
    assert File("alpha.cast").url == f"{DISK_BASE}alpha.cast"


def test_the_filer_url_it_is_given_is_used_with_or_without_a_slash() -> None:
    assert File("alpha.cast", filer_url=FILER).url == f"{FILER}alpha.cast"
    assert File("alpha.cast", filer_url=FILER.rstrip("/")).url == f"{FILER}alpha.cast"


def test_the_filer_gets_put_get_and_delete_on_the_file_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = filer(monkeypatch)
    file = File("alpha/beta.cast", b"gamma", FILER)

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


def test_the_filer_gets_append_as_a_post_and_size_as_a_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = filer(monkeypatch)
    file = File("alpha/beta.log", b"gamma\x00delta", FILER)

    file.append()

    assert file.size() == 5
    assert [(request.method, str(request.url)) for request in requests] == [
        ("POST", f"{file.url}?op=append"),
        ("HEAD", file.url),
    ]
    assert requests[0].content == b"gamma\x00delta"


@pytest.mark.parametrize("path", BAD_PATHS)
def test_a_path_that_is_not_plain_and_relative_is_refused(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="not a relative file path"):
        File(path, b"alpha")

    requests = filer(monkeypatch)
    with pytest.raises(ValueError, match="not a relative file path"):
        File(path, b"alpha", FILER)
    assert requests == []


@pytest.mark.parametrize("status", [301, 404, 500])
def test_a_filer_answer_other_than_2xx_raises(status: int, monkeypatch: pytest.MonkeyPatch) -> None:
    filer(monkeypatch, status)
    file = File("alpha.cast", b"beta", FILER)

    with pytest.raises(httpx.HTTPStatusError):
        file.put()
    with pytest.raises(httpx.HTTPStatusError):
        file.get()
    with pytest.raises(httpx.HTTPStatusError):
        file.append()


@pytest.mark.parametrize("status", [301, 500])
def test_a_filer_error_other_than_404_raises_from_size(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    filer(monkeypatch, status)

    with pytest.raises(httpx.HTTPStatusError):
        File("alpha.log", filer_url=FILER).size()
