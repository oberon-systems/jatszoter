import time
import uuid
from collections.abc import Callable
from enum import StrEnum
from typing import Any, Self

from pydantic import PrivateAttr
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, select

from jatszoter_api.errors import DB_ERRORS
from jatszoter_api.models._cache import Cached


def _code(origin: BaseException | None) -> str | None:
    code: str | None = getattr(origin, "sqlstate", None)
    return code or getattr(origin, "sqlite_errorname", None)


class State(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class Base(SQLModel, Cached):
    """`X(session=session, **fields)._save()` stores a row; `X._load(session, **key)` reads rows."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: int = Field(default_factory=lambda: int(time.time()))
    _session: Session = PrivateAttr()

    def __init__(self, session: Session, **data: Any) -> None:
        super().__init__(**data)
        self._session = session

    @classmethod
    def _load(cls, session: Session, **key: Any) -> list[Self]:
        stored = list(session.exec(select(cls).filter_by(**key)))
        for row in stored:
            row.model_post_init(None)
            row._session = session
        return stored

    def _save(self) -> None:
        self._session.add(self)
        try:
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            if known := DB_ERRORS.get(_code(error.orig)):
                raise known(str(error.orig)) from error
            raise

    def _get(self, key: str, load: Callable[[], Any] | None = None, ttl: int = 10) -> Any | None:
        """`<key>:<id>` from the cache, renewed for `ttl` s on a hit; a miss stores `load()`."""
        value = super()._get(f"{key}:{self.id}")
        if value is not None:
            super()._ttl(f"{key}:{self.id}", ttl)
            return value
        if load is None:
            return None
        value = load()
        super()._set(f"{key}:{self.id}", value, ttl)
        return value

    def _set(self, key: str, value: Any, ttl: int = 10) -> None:
        super()._set(f"{key}:{self.id}", value, ttl)

    def _ttl(self, key: str, ttl: int = 10) -> bool:
        return super()._ttl(f"{key}:{self.id}", ttl)

    def _delete(self, key: str) -> bool:
        return super()._delete(f"{key}:{self.id}")
