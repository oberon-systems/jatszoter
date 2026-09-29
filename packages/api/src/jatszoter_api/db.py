from collections.abc import Iterator

from fastapi import Request
from sqlmodel import Session


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session
