import hashlib
import hmac
import uuid
from typing import Annotated, Self

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from jatszoter_api.auth import require_agent, unauthorized
from jatszoter_api.models import Agent, Credential
from jatszoter_api.models.agent import Lease
from jatszoter_api.models.secret import CREDENTIAL_SECONDS
from jatszoter_api.routes.deps import SessionDep

AgentDep = Annotated[Agent, Depends(require_agent)]

Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
Label = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterIn(StrictModel):
    name: Name
    labels: Annotated[list[Label], Field(max_length=16)] = []
    agent_id: uuid.UUID | None = None


class RegisterOut(BaseModel):
    claim_code: str


class ClaimIn(StrictModel):
    code: Annotated[str, Field(min_length=1, max_length=128)]


class CredentialOut(BaseModel):
    """`credential` goes verbatim into `X-Credential`; `key` signs, and is shown only once."""

    credential: str
    expires_at: int
    key: str

    @classmethod
    def of(cls, credential: Credential, key: str) -> Self:
        return cls(
            credential=f"{credential.kid}:{credential.generation}:{credential.expires_at}",
            expires_at=credential.expires_at,
            key=key,
        )


class ClaimOut(BaseModel):
    agent_id: uuid.UUID
    credential: CredentialOut


class HeartbeatIn(StrictModel):
    capacity: Annotated[StrictInt, Field(ge=0, le=64)]


router = APIRouter(prefix="/agents")


@router.post("/register", status_code=201)
def register(body: RegisterIn, session: SessionDep) -> RegisterOut:
    agent = Agent(session=session, name=body.name, labels=list(body.labels))
    if body.agent_id is not None:
        agent.id = body.agent_id
    return RegisterOut(claim_code=agent.claim_code())


@router.post("/claim")
def claim(body: ClaimIn, session: SessionDep) -> Response:
    agent = Agent.claimed(session, body.code)
    if agent is None or not agent.redeem(body.code):
        raise unauthorized()
    credential, key = agent.issue(CREDENTIAL_SECONDS)
    payload = ClaimOut(agent_id=agent.id, credential=CredentialOut.of(credential, key))
    content = payload.model_dump_json().encode()
    signature = hmac.new(body.code.encode(), content, hashlib.sha256).hexdigest()
    return Response(content, media_type="application/json", headers={"X-Signature": signature})


@router.post("/{agent_id}/heartbeat")
def heartbeat(body: HeartbeatIn, agent: AgentDep) -> Lease:
    return agent.heartbeat(body.capacity)


@router.post("/{agent_id}/credential")
def refresh(agent: AgentDep) -> CredentialOut:
    return CredentialOut.of(*agent.issue(CREDENTIAL_SECONDS))
