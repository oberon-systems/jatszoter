import stat
from pathlib import Path

import httpx
import pytest

from jatszoter_api.files import DISK_BASE, Files, get_files
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
FOREIGN_URLS = [
    "http://beta.example.com/alpha",
    "file:///etc/passwd",
    "{base}../alpha",
    "{base}alpha/%2e%2e/%2e%2e/beta",
    "{base}alpha%2F..%2F..%2Fbeta",
    "{base}alpha?recursive=true",
    "{base}",
]


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def filer(requests: list[httpx.Request], status: int = 200) -> Files:
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, content=b"alpha" if request.method == "GET" else b"")

    return Files(FILER, httpx.Client(transport=httpx.MockTransport(handle)))


def test_a_local_file_round_trips_private(tmp_path: Path) -> None:
    files = get_files()

    url = files.put("alpha/beta gamma.cast", b"delta")

    root = tmp_path / "data" / "storage"
    target = root / "alpha" / "beta gamma.cast"
    assert url == f"{DISK_BASE}alpha/beta%20gamma.cast"
    assert target.read_bytes() == b"delta"
    assert mode(root) == mode(root / "alpha") == 0o700
    assert mode(target) == 0o600
    assert files.get(url) == b"delta"

    files.delete(url)

    assert not target.exists()
    with pytest.raises(httpx.HTTPStatusError):
        files.get(url)


def test_a_local_put_replaces_the_file(tmp_path: Path) -> None:
    files = get_files()
    url = files.put("alpha.cast", b"beta")

    assert files.put("alpha.cast", b"gamma") == url

    assert files.get(url) == b"gamma"
    assert [path.name for path in (tmp_path / "data" / "storage").iterdir()] == ["alpha.cast"]


def test_without_a_filer_files_go_to_the_data_dir() -> None:
    assert get_files().base == DISK_BASE


def test_the_filer_is_used_when_its_url_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JATSZOTER_SEAWEED_URL", FILER.rstrip("/"))
    get_settings.cache_clear()

    assert get_files().base == FILER


def test_the_filer_gets_put_get_and_delete_on_the_file_url() -> None:
    requests: list[httpx.Request] = []
    files = filer(requests)

    url = files.put("alpha/beta.cast", b"gamma")
    assert files.get(url) == b"alpha"
    files.delete(url)

    assert url == f"{FILER}alpha/beta.cast"
    assert [(request.method, str(request.url)) for request in requests] == [
        ("PUT", url),
        ("GET", url),
        ("DELETE", url),
    ]
    assert requests[0].content == b"gamma"


@pytest.mark.parametrize("path", BAD_PATHS)
def test_a_path_that_is_not_plain_and_relative_is_refused(path: str) -> None:
    requests: list[httpx.Request] = []

    for files in (get_files(), filer(requests)):
        with pytest.raises(ValueError, match="not a relative file path"):
            files.put(path, b"alpha")

    assert requests == []


@pytest.mark.parametrize("url", FOREIGN_URLS)
def test_a_url_outside_the_store_is_refused(url: str) -> None:
    requests: list[httpx.Request] = []

    for files in (get_files(), filer(requests)):
        with pytest.raises(ValueError):
            files.get(url.format(base=files.base))
        with pytest.raises(ValueError):
            files.delete(url.format(base=files.base))

    assert requests == []


@pytest.mark.parametrize("status", [301, 404, 500])
def test_a_filer_answer_other_than_2xx_raises(status: int) -> None:
    files = filer([], status)

    with pytest.raises(httpx.HTTPStatusError):
        files.put("alpha.cast", b"beta")
    with pytest.raises(httpx.HTTPStatusError):
        files.get(f"{FILER}alpha.cast")
