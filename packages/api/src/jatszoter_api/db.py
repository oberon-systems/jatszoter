from collections.abc import Iterator

from fastapi import Request
from sqlmodel import Session, create_engine

import jatszoter_api.models  # noqa: F401  registers the tables on SQLModel.metadata
from jatszoter_api.models._base import use_session


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_engine(url)


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.db.engine) as session, use_session(session):
        yield session
