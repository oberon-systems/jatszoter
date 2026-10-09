import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, WebSocket, status
from sqlmodel import Session

from jatszoter_api.db import get_session
from jatszoter_api.models import Agent, Credential, Role, User
from jatszoter_api.models.secret import SecretOwner
from jatszoter_api.settings import Settings, app_settings


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


def _owner(x_owner: str) -> uuid.UUID:
    try:
        return uuid.UUID(x_owner)
    except ValueError:
        raise unauthorized() from None


def _signed[T: SecretOwner](
    model: type[T],
    owner_id: uuid.UUID,
    method: str,
    path: str,
    session: Session,
    keys: dict[str, str],
    body: bytes,
    x_credential: str,
    x_timestamp: str,
    x_signature: str,
) -> T:
    """The owner `owner_id` names, once its signed request checks out."""
    credential = _credential(owner_id, x_credential)
    if credential is None or not x_timestamp.isdigit():
        raise unauthorized()
    try:
        owner = model(session=session).load(id=owner_id)
    except IndexError:
        raise unauthorized() from None
    if not owner.verify(credential, keys, method, path, body, int(x_timestamp), x_signature):
        raise unauthorized()
    return owner


def require_agent(
    agent_id: uuid.UUID,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(app_settings)],
    body: Annotated[bytes, Depends(_body)],
    x_credential: Annotated[str, Header()] = "",
    x_timestamp: Annotated[str, Header()] = "",
    x_signature: Annotated[str, Header()] = "",
) -> Agent:
    """The agent named in the path, once its signed request checks out."""
    return _signed(
        Agent,
        agent_id,
        request.method,
        request.url.path,
        session,
        settings.master_keys,
        body,
        x_credential,
        x_timestamp,
        x_signature,
    )


def require_user(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(app_settings)],
    body: Annotated[bytes, Depends(_body)],
    x_owner: Annotated[str, Header()] = "",
    x_credential: Annotated[str, Header()] = "",
    x_timestamp: Annotated[str, Header()] = "",
    x_signature: Annotated[str, Header()] = "",
) -> User:
    """The user named in `X-Owner`, once its signed request checks out."""
    return _signed(
        User,
        _owner(x_owner),
        request.method,
        request.url.path,
        session,
        settings.master_keys,
        body,
        x_credential,
        x_timestamp,
        x_signature,
    )


def require_operator(user: Annotated[User, Depends(require_user)]) -> User:
    """The signed user, refused with 403 unless their role is operator."""
    if user.role is not Role.OPERATOR:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return user


def socket_agent(
    agent_id: uuid.UUID, websocket: WebSocket, session: Session, keys: dict[str, str]
) -> Agent:
    """The agent named in the socket's path, once its upgrade, signed as a GET, checks out."""
    headers = websocket.headers
    return _signed(
        Agent,
        agent_id,
        "GET",
        websocket.url.path,
        session,
        keys,
        b"",
        headers.get("x-credential", ""),
        headers.get("x-timestamp", ""),
        headers.get("x-signature", ""),
    )


def socket_operator(websocket: WebSocket, session: Session, keys: dict[str, str]) -> User:
    """The operator named in `X-Owner`, once the socket's upgrade, signed as a GET, checks out."""
    headers = websocket.headers
    user = _signed(
        User,
        _owner(headers.get("x-owner", "")),
        "GET",
        websocket.url.path,
        session,
        keys,
        b"",
        headers.get("x-credential", ""),
        headers.get("x-timestamp", ""),
        headers.get("x-signature", ""),
    )
    return require_operator(user)
