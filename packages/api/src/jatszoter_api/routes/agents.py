import hashlib
import hmac
import uuid
from typing import Annotated, Literal, Self

import httpx
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from sqlmodel import Session

from jatszoter_api import bundles
from jatszoter_api.auth import require_agent, unauthorized
from jatszoter_api.errors import ConflictError, DomainError, NotFoundError
from jatszoter_api.file import File
from jatszoter_api.lifecycle import Actor, DeploymentStatus
from jatszoter_api.models import Agent, Credential, Deployment
from jatszoter_api.models.agent import Lease
from jatszoter_api.models.secret import CREDENTIAL_SECONDS
from jatszoter_api.routes.deps import SessionDep, SettingsDep

AgentDep = Annotated[Agent, Depends(require_agent)]


def leased(agent: AgentDep) -> Agent:
    """The signed agent, refused with 409 unless its lease is live."""
    if agent.lease is None:
        raise ConflictError(f"agent {agent.id} holds no live lease")
    return agent


LeasedDep = Annotated[Agent, Depends(leased)]

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


def credential_out(credential: Credential, key: str) -> CredentialOut:
    return CredentialOut(
        credential=f"{credential.kid}:{credential.generation}:{credential.expires_at}",
        expires_at=credential.expires_at,
        key=key,
    )


class ClaimOut(BaseModel):
    agent_id: uuid.UUID
    credential: CredentialOut


class HeartbeatIn(StrictModel):
    capacity: Annotated[StrictInt, Field(ge=0, le=64)]


class ScenarioRef(BaseModel):
    id: uuid.UUID
    slug: str
    version: int


class ImageRef(BaseModel):
    name: str
    url: str
    digest: str


class Resources(BaseModel):
    cpus: int
    memory_mib: int
    disk_gib: int


class Timeouts(BaseModel):
    bootstrap_seconds: int
    check_seconds: int
    session_minutes: int


class DesiredOut(BaseModel):
    id: uuid.UUID
    vm_id: uuid.UUID
    status: DeploymentStatus
    scenario: ScenarioRef
    image: ImageRef
    resources: Resources
    timeouts: Timeouts


class TransitionIn(StrictModel):
    event: Literal["assign", "boot", "bootstrap", "destroy", "fail"]
    expected: DeploymentStatus
    reason: Annotated[str, Field(min_length=1, max_length=1024)] | None = None

    @model_validator(mode="after")
    def _reasoned(self) -> Self:
        if self.event == "fail" and self.reason is None:
            raise ValueError("fail needs a reason")
        return self


class TransitionOut(BaseModel):
    id: uuid.UUID
    status: DeploymentStatus


class SeedOut(BaseModel):
    user_data: str
    meta_data: str


router = APIRouter(prefix="/agents")


def _held(session: Session, agent: Agent, deployment_id: uuid.UUID) -> Deployment:
    try:
        return Deployment(session=session).load(id=deployment_id, agent_id=agent.id)
    except IndexError:
        raise NotFoundError(f"deployment {deployment_id} is not known") from None


@router.post("/register", status_code=201)
def register(body: RegisterIn, session: SessionDep, settings: SettingsDep) -> RegisterOut:
    known = {"id": body.agent_id} if body.agent_id is not None else {}
    agent = Agent(session=session, name=body.name, labels=list(body.labels), **known)
    return RegisterOut(claim_code=agent.claim_code(settings.auto_register_agents))


@router.post("/claim")
def claim(body: ClaimIn, session: SessionDep, settings: SettingsDep) -> Response:
    agent = Agent(session=session).claimed(body.code)
    if agent is None or not agent.redeem(body.code):
        raise unauthorized()
    credential, key = agent.issue(CREDENTIAL_SECONDS, settings.master_kid, settings.master_keys)
    payload = ClaimOut(agent_id=agent.id, credential=credential_out(credential, key))
    content = payload.model_dump_json().encode()
    signature = hmac.new(body.code.encode(), content, hashlib.sha256).hexdigest()
    return Response(content, media_type="application/json", headers={"X-Signature": signature})


@router.post("/{agent_id}/heartbeat")
def heartbeat(body: HeartbeatIn, agent: AgentDep) -> Lease:
    return agent.heartbeat(body.capacity)


@router.post("/{agent_id}/credential")
def refresh(agent: AgentDep, settings: SettingsDep) -> CredentialOut:
    return credential_out(
        *agent.issue(CREDENTIAL_SECONDS, settings.master_kid, settings.master_keys)
    )


@router.get("/{agent_id}/deployments")
def desired(agent: LeasedDep) -> list[DesiredOut]:
    return [DesiredOut.model_validate(entry) for entry in agent.desired()]


@router.post("/{agent_id}/deployments/{deployment_id}/transition")
def transition(
    body: TransitionIn, agent: LeasedDep, session: SessionDep, deployment_id: uuid.UUID
) -> TransitionOut:
    deployment = _held(session, agent, deployment_id)
    deployment.transition(
        body.event, body.expected, body.reason, actor=Actor.AGENT, agent_id=agent.id
    )
    return TransitionOut(id=deployment.id, status=deployment.status)


@router.get("/{agent_id}/deployments/{deployment_id}/seed")
def seed(
    agent: LeasedDep, session: SessionDep, settings: SettingsDep, deployment_id: uuid.UUID
) -> SeedOut:
    deployment = _held(session, agent, deployment_id)
    if deployment.status is not DeploymentStatus.STARTING:
        raise ConflictError(f"deployment {deployment_id} is {deployment.status}, not STARTING")
    scenario = deployment.scenario
    filer_url = None if settings.seaweed_url is None else str(settings.seaweed_url)
    stored = File(scenario.bundle_path, filer_url=filer_url)
    # Not a DomainError: a stored bundle that no longer reads is the API's fault, a 500.
    try:
        stored.get()
        bundle = bundles.read(stored.data, settings.scenario_bundle_limit_bytes)
    except (httpx.HTTPError, DomainError):
        raise RuntimeError(f"the stored bundle of scenario {scenario.id} does not read") from None
    if bundle.digest != scenario.digest:
        raise RuntimeError(f"the stored bundle of scenario {scenario.id} changed")
    rendered = deployment.seed(
        bundle.files, settings.default_trainee_user, settings.default_trainee_sudo
    )
    return SeedOut(user_data=rendered.user_data, meta_data=rendered.meta_data)
