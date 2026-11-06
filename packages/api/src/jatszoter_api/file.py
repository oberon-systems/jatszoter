import base64
import hashlib
import hmac
import json
import os
import tempfile
import time
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote

import httpx

TIMEOUT = 30.0
TOKEN_SECONDS = 60
DATA_DIR = Path("data/storage")
# Where the disk filer answers; its transport never opens a connection.
DISK_BASE = "http://files.invalid/"


def _segments(path: str) -> list[str]:
    segments = path.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ValueError(f"not a relative file path: {path!r}")
    return segments


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def filer_token(key: bytes, now: int) -> str:
    """An HS256 JWT the filer accepts under `jwt.filer_signing` and `jwt.filer_signing.read`."""
    claims = json.dumps({"exp": now + TOKEN_SECONDS}).encode()
    signed = _b64(b'{"alg":"HS256","typ":"JWT"}') + "." + _b64(claims)
    return signed + "." + _b64(hmac.new(key, signed.encode(), hashlib.sha256).digest())


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

    _jwt_key: ClassVar[bytes | None] = None

    @classmethod
    def connect(cls, jwt_key: str | None) -> None:
        """Sign every call to the filer with `jwt_key`, the filer's own signing key; none
        without one."""
        cls._jwt_key = None if jwt_key is None else jwt_key.encode()

    def __init__(self, path: str, data: bytes = b"", filer_url: str | None = None) -> None:
        transport: httpx.BaseTransport | None = None
        if filer_url is not None:
            base = filer_url.rstrip("/") + "/"
        else:
            base, transport = DISK_BASE, DiskFiler(DATA_DIR.absolute())
        _segments(path)
        self.url = base + quote(path)
        self.data = data
        self._key = None if filer_url is None else self._jwt_key
        self._client = httpx.Client(transport=transport, timeout=TIMEOUT)

    def _headers(self) -> dict[str, str]:
        if self._key is None:
            return {}
        return {"authorization": f"Bearer {filer_token(self._key, int(time.time()))}"}

    def put(self) -> None:
        self._client.put(self.url, content=self.data, headers=self._headers()).raise_for_status()

    def append(self) -> None:
        self._client.post(
            self.url, params={"op": "append"}, content=self.data, headers=self._headers()
        ).raise_for_status()

    def size(self) -> int:
        response = self._client.head(self.url, headers=self._headers())
        if response.status_code == 404:
            return 0
        return int(response.raise_for_status().headers["Content-Length"])

    def get(self) -> None:
        self.data = self._client.get(self.url, headers=self._headers()).raise_for_status().content

    def delete(self) -> None:
        response = self._client.delete(self.url, headers=self._headers())
        if response.status_code != 404:
            response.raise_for_status()
