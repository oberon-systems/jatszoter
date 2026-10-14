import os
import tempfile
from pathlib import Path
from urllib.parse import quote

import httpx

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
    """The SeaweedFS filer's PUT, append, GET, HEAD and DELETE, answered from a local directory."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        target = self._root.joinpath(*_segments(request.url.path.removeprefix("/")))
        if request.method == "PUT":
            self._write(target, request.read())
            return httpx.Response(201)
        if request.method == "POST" and request.url.params.get("op") == "append":
            self._append(target, request.read())
            return httpx.Response(201)
        if request.method == "GET":
            if not target.is_file():
                return httpx.Response(404)
            return httpx.Response(200, content=target.read_bytes())
        if request.method == "HEAD":
            if not target.is_file():
                return httpx.Response(404)
            return httpx.Response(200, headers={"Content-Length": str(target.stat().st_size)})
        if request.method == "DELETE":
            target.unlink(missing_ok=True)
            return httpx.Response(204)
        return httpx.Response(405)

    def _parents(self, target: Path) -> None:
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        parts = target.relative_to(self._root).parts
        for depth in range(1, len(parts)):
            self._root.joinpath(*parts[:depth]).mkdir(mode=0o700, exist_ok=True)

    def _append(self, target: Path, data: bytes) -> None:
        self._parents(target)
        fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "ab") as file:
            file.write(data)

    def _write(self, target: Path, data: bytes) -> None:
        self._parents(target)
        fd, temp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
            os.replace(temp, target)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise


class File:
    """One file the API keeps under `path`, on the filer or on local disk."""

    def __init__(self, path: str, data: bytes = b"", filer_url: str | None = None) -> None:
        transport: httpx.BaseTransport | None = None
        if filer_url is not None:
            base = filer_url.rstrip("/") + "/"
        else:
            base, transport = DISK_BASE, DiskFiler(DATA_DIR.absolute())
        _segments(path)
        self.url = base + quote(path)
        self.data = data
        self._client = httpx.Client(transport=transport, timeout=TIMEOUT)

    def put(self) -> None:
        self._client.put(self.url, content=self.data).raise_for_status()

    def append(self) -> None:
        self._client.post(self.url, params={"op": "append"}, content=self.data).raise_for_status()

    def size(self) -> int:
        response = self._client.head(self.url)
        if response.status_code == 404:
            return 0
        return int(response.raise_for_status().headers["Content-Length"])

    def get(self) -> None:
        self.data = self._client.get(self.url).raise_for_status().content

    def delete(self) -> None:
        response = self._client.delete(self.url)
        if response.status_code != 404:
            response.raise_for_status()
