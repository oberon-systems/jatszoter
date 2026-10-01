import hashlib
import hmac
import io
import json
import secrets
import tarfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

import jatszoter_api
from jatszoter_api.app import create_app
from jatszoter_api.models import Role, State, User
from jatszoter_api.models._cache import Cached
from jatszoter_api.settings import get_settings

MIGRATIONS = Path(jatszoter_api.__file__).parent / "migrations"
MASTER_KEYS = {"old": secrets.token_hex(32), "current": secrets.token_hex(32)}
SAMPLE = Path(__file__).resolve().parents[3] / "scenarios" / "nginx-502"
PASSWORD = "correct horse battery"

Member = tuple[tarfile.TarInfo, bytes]
Pack = Callable[..., bytes]


class Signed:
    """A logged-in user's signed requests, the way an operator client sends them."""

    def __init__(self, client: TestClient, answer: dict[str, Any]) -> None:
        self.client = client
        self.id = answer["user_id"]
        self.credential = answer["credential"]["credential"]
        self.key = answer["credential"]["key"]

    def request(self, method: str, path: str, **content: Any) -> httpx.Response:
        request = self.client.build_request(method, path, **content)
        timestamp = str(int(time.time()))
        body_hash = hashlib.sha256(request.read()).hexdigest()
        message = "\n".join((timestamp, method, request.url.path, body_hash))
        request.headers["X-Owner"] = self.id
        request.headers["X-Credential"] = self.credential
        request.headers["X-Timestamp"] = timestamp
        request.headers["X-Signature"] = hmac.new(
            self.key.encode(), message.encode(), hashlib.sha256
        ).hexdigest()
        response: httpx.Response = self.client.send(request)
        return response


@pytest.fixture
def database_dsn(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'jatszoter.db'}"


@pytest.fixture(autouse=True)
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, database_dsn: str) -> Iterator[None]:
    monkeypatch.setenv("JATSZOTER_DATABASE_DSN", database_dsn)
    monkeypatch.delenv("JATSZOTER_VALKEY_DSN", raising=False)
    monkeypatch.delenv("JATSZOTER_SEAWEED_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JATSZOTER_MASTER_KEYS", json.dumps(MASTER_KEYS))
    monkeypatch.setenv("JATSZOTER_MASTER_KID", "current")
    get_settings.cache_clear()
    Cached._client.cache_clear()
    yield
    get_settings.cache_clear()
    Cached._client.cache_clear()


@pytest.fixture
def alembic_config(database_dsn: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", database_dsn)
    return config


@pytest.fixture
def session(database_dsn: str, alembic_config: Config) -> Iterator[Session]:
    command.upgrade(alembic_config, "head")
    engine = create_engine(database_dsn)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def api(session: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def sign_in(api: TestClient, session: Session) -> Callable[..., Signed]:
    """Log in a new enabled user of `role` and sign their requests."""

    def signed(name: str = "alpha", role: Role = Role.OPERATOR) -> Signed:
        user = User(
            session=session,
            name=name,
            role=role,
            state=State.ENABLED,
            password=PASSWORD,
            password_changed=1,
        )
        user.save()
        answer = api.post("/api/v1/auth/login", json={"name": name, "password": PASSWORD})
        assert answer.status_code == 200
        return Signed(api, answer.json())

    return signed


@pytest.fixture
def operator(sign_in: Callable[..., Signed]) -> Signed:
    return sign_in()


def _pack(
    top: str | None = "nginx-502",
    mtime: int = 0,
    changes: Mapping[str, bytes | None] | None = None,
    extra: Sequence[Member] = (),
) -> bytes:
    """A tar.gz of the sample scenario: `changes` replaces (None drops) files by name."""
    files = {path.name: path.read_bytes() for path in sorted(SAMPLE.iterdir())}
    for name, content in (changes or {}).items():
        if content is None:
            files.pop(name, None)
        else:
            files[name] = content
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        if top is not None:
            directory = tarfile.TarInfo(top)
            directory.type, directory.mtime = tarfile.DIRTYPE, mtime
            tar.addfile(directory)
        for name, content in files.items():
            info = tarfile.TarInfo(f"{top}/{name}" if top else name)
            info.size, info.mtime = len(content), mtime
            tar.addfile(info, io.BytesIO(content))
        for info, content in extra:
            tar.addfile(info, io.BytesIO(content) if content else None)
    return buffer.getvalue()


@pytest.fixture
def pack() -> Pack:
    return _pack
