import json
import secrets
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlmodel import Session, create_engine

import jatszoter_api
from jatszoter_api.models._cache import Cached
from jatszoter_api.settings import get_settings

MIGRATIONS = Path(jatszoter_api.__file__).parent / "migrations"
MASTER_KEYS = {"old": secrets.token_hex(32), "current": secrets.token_hex(32)}


@pytest.fixture
def database_dsn(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'jatszoter.db'}"


@pytest.fixture(autouse=True)
def settings(monkeypatch: pytest.MonkeyPatch, database_dsn: str) -> Iterator[None]:
    monkeypatch.setenv("JATSZOTER_DATABASE_DSN", database_dsn)
    monkeypatch.delenv("JATSZOTER_VALKEY_DSN", raising=False)
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
