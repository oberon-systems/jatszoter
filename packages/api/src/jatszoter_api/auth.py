import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlmodel import Session

from jatszoter_api.db import get_session
from jatszoter_api.models import Agent, Credential


def unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authorized")


async def _body(request: Request) -> bytes:
    return await request.body()


def _credential(owner_id: uuid.UUID, header: str) -> Credential | None:
    kid, _, expires_at = header.rpartition(":")
    kid, _, generation = kid.rpartition(":")
    try:
        return Credential(owner_id, kid, int(generation), int(expires_at))
    except ValueError:
        return None


def require_agent(
    agent_id: uuid.UUID,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    body: Annotated[bytes, Depends(_body)],
    x_credential: Annotated[str, Header()] = "",
    x_timestamp: Annotated[str, Header()] = "",
    x_signature: Annotated[str, Header()] = "",
) -> Agent:
    """The agent named in the path, once its signed request checks out."""
    credential = _credential(agent_id, x_credential)
    if credential is None or not x_timestamp.isdigit():
        raise unauthorized()
    try:
        agent = Agent.load(session, id=agent_id)
    except IndexError:
        raise unauthorized() from None
    if not agent.verify(
        credential, request.method, request.url.path, body, int(x_timestamp), x_signature
    ):
        raise unauthorized()
    return agent
