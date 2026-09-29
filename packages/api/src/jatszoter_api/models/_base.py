import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import Any, Self

from sqlmodel import (
    Field,
    PrimaryKeyConstraint,
    Session,
    SQLModel,
    UniqueConstraint,
    col,
    select,
)

from jatszoter_api.errors import NotFoundError

_current: ContextVar[Session | None] = ContextVar("session", default=None)


@contextmanager
def use_session(session: Session) -> Iterator[Session]:
    token = _current.set(session)
    try:
        yield session
    finally:
        _current.reset(token)


def current_session() -> Session:
    session = _current.get()
    if session is None:
        raise RuntimeError("no session in use")
    return session


def session_of(instance: SQLModel) -> Session:
    session = Session.object_session(instance)
    if not isinstance(session, Session):
        raise RuntimeError(f"{type(instance).__name__} is not attached to a session")
    return session


class State(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class Base(SQLModel):
    """`X(**fields)` adds a new row to the current session; `X.get(**key)` opens a stored one."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: int = Field(default_factory=lambda: int(time.time()))

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        current_session().add(self)

    @classmethod
    def get(cls, **key: Any) -> Self:
        """The row found by `id`, a unique field or a composite unique key."""
        if frozenset(key) not in cls._lookup_keys():
            raise ValueError(f"{cls.__name__} cannot be found by {sorted(key)}")
        session = current_session()
        if set(key) == {"id"}:
            found = session.get(cls, key["id"])
        else:
            statement = select(cls).where(*(col(getattr(cls, k)) == v for k, v in key.items()))
            found = session.exec(statement).first()
        if found is None:
            raise NotFoundError(f"{cls.__name__} {key} not found")
        return found

    def set(self, **fields: Any) -> Self:
        """Change fields and commit."""
        unknown = set(fields) - (set(type(self).model_fields) - {"id"})
        if unknown:
            raise ValueError(f"{type(self).__name__} has no settable {sorted(unknown)}")
        for name, value in fields.items():
            setattr(self, name, value)
        session_of(self).commit()
        return self

    @classmethod
    def _lookup_keys(cls) -> frozenset[frozenset[str]]:
        table = cls.metadata.tables.get(str(cls.__tablename__))
        if table is None:
            return frozenset()
        keys = {
            frozenset(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, PrimaryKeyConstraint | UniqueConstraint)
        }
        unique = {frozenset({column.name}) for column in table.columns if column.unique}
        return frozenset(keys | unique)
