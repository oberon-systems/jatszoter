import time
import uuid
from enum import StrEnum
from typing import Any, Self

from pydantic import PrivateAttr
from sqlmodel import Field, Session, SQLModel, select


class State(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class Base(SQLModel):
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
        self._session.commit()
