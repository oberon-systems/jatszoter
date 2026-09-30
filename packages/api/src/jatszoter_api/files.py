import functools
import os
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote

import httpx

from jatszoter_api.settings import get_settings

TIMEOUT = 30.0
DATA_DIR = Path("data/storage")
# Where the disk filer answers; its transport never opens a connection.
DISK_BASE = "http://files.invalid/"


def _segments(path: str) -> list[str]:
    segments = path.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ValueError(f"not a relative file path: {path!r}")
    return segments


class DiskFiler(httpx.BaseTransport):
    """The SeaweedFS filer's PUT, GET and DELETE, answered from a local directory."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        target = self._root.joinpath(*_segments(request.url.path.removeprefix("/")))
        if request.method == "PUT":
            self._write(target, request.read())
            return httpx.Response(201)
        if request.method == "GET":
            if not target.is_file():
                return httpx.Response(404)
            return httpx.Response(200, content=target.read_bytes())
        if request.method == "DELETE":
            target.unlink(missing_ok=True)
            return httpx.Response(204)
        return httpx.Response(405)

    def _write(self, target: Path, data: bytes) -> None:
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        parts = target.relative_to(self._root).parts
        for depth in range(1, len(parts)):
            self._root.joinpath(*parts[:depth]).mkdir(mode=0o700, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
            os.replace(temp, target)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise


class Files:
    """Files the API keeps, each addressed by the URL `put` returned for it."""

    def __init__(self, base: str, client: httpx.Client) -> None:
        self.base = base
        self._client = client

    def put(self, path: str, data: bytes) -> str:
        _segments(path)
        url = self.base + quote(path)
        self._client.put(url, content=data).raise_for_status()
        return url

    def get(self, url: str) -> bytes:
        return self._client.get(self._checked(url)).raise_for_status().content

    def delete(self, url: str) -> None:
        self._client.delete(self._checked(url)).raise_for_status()

    def _checked(self, url: str) -> str:
        path = unquote(url.removeprefix(self.base))
        if not url.startswith(self.base) or self.base + quote(path) != url:
            raise ValueError("file URL outside the store")
        _segments(path)
        return url


@functools.cache
def get_files() -> Files:
    settings = get_settings()
    transport: httpx.BaseTransport | None
    if settings.seaweed_url is not None:
        base, transport = str(settings.seaweed_url).rstrip("/") + "/", None
    else:
        base, transport = DISK_BASE, DiskFiler(DATA_DIR.absolute())
    return Files(base, httpx.Client(transport=transport, timeout=TIMEOUT))
