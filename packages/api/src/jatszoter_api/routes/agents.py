import hashlib
import hmac
import uuid
from typing import Annotated, Literal, Self

import httpx
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from sqlmodel import Session

from jatszoter_api import bundles
from jatszoter_api.auth import require_agent, require_operator, unauthorized
from jatszoter_api.errors import ConflictError, DomainError, NotFoundError
from jatszoter_api.file import File
from jatszoter_api.lifecycle import Actor, DeploymentStatus
from jatszoter_api.models import Agent, Check, CheckStatus, Credential, Deployment, State
from jatszoter_api.models.agent import Lease, Registration
from jatszoter_api.models.secret import CREDENTIAL_SECONDS
from jatszoter_api.routes.deps import MarkDep, SessionDep, SettingsDep
from jatszoter_api.settings import Settings

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
    check: uuid.UUID | None


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


class ScriptOut(BaseModel):
    script: str
    timeout_seconds: int


class ResultIn(StrictModel):
    status: Literal["passed", "failed", "error", "timeout"]
    exit_code: Annotated[StrictInt, Field(ge=-(2**31), lt=2**31)] | None = None
    message: str | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.status == "passed":
            fits = self.exit_code == 0
        elif self.status == "failed":
            fits = self.exit_code not in (None, 0)
        else:
            fits = self.exit_code is None
        if not fits:
            raise ValueError(f"{self.status} does not go with this exit code")
        return self


class ResultOut(BaseModel):
    id: uuid.UUID
    status: CheckStatus


class AgentOut(BaseModel):
    id: uuid.UUID
    name: str
    labels: list[str]
    state: State
    heartbeat_at: int | None
    capacity: int | None
    lease_expires_at: int | None


def agent_out(agent: Agent) -> AgentOut:
    lease = agent.lease
    return AgentOut(
        id=agent.id,
        name=agent.name,
        labels=agent.labels,
        state=agent.state,
        heartbeat_at=None if lease is None else lease.heartbeat_at,
        capacity=None if lease is None else lease.capacity,
        lease_expires_at=None if lease is None else lease.expires_at,
    )


class AgentsOut(BaseModel):
    registrations: list[Registration]
    agents: list[AgentOut]


class StateIn(StrictModel):
    state: State


router = APIRouter(prefix="/agents")


def _held(session: Session, agent: Agent, deployment_id: uuid.UUID) -> Deployment:
    try:
        return Deployment(session=session).load(id=deployment_id, agent_id=agent.id)
    except IndexError:
        raise NotFoundError(f"deployment {deployment_id} is not known") from None


def _pending(
    session: Session, agent: Agent, deployment_id: uuid.UUID, check_id: uuid.UUID
) -> tuple[Deployment, Check]:
    """The agent's READY deployment and its pending check; anything else is refused."""
    deployment = _held(session, agent, deployment_id)
    try:
        check = Check(session=session).load(id=check_id, deployment_id=deployment.id)
    except IndexError:
        raise NotFoundError(f"check {check_id} is not known") from None
    if deployment.status is not DeploymentStatus.READY:
        raise ConflictError(f"deployment {deployment_id} is {deployment.status}, not READY")
    if check.status is not CheckStatus.PENDING:
        raise ConflictError(f"check {check_id} is {check.status}")
    return deployment, check


def stored_bundle(deployment: Deployment, settings: Settings) -> bundles.Bundle:
    """The stored bundle of the deployment's scenario, read and compared with its digest."""
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
    return bundle


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
def heartbeat(body: HeartbeatIn, agent: AgentDep, settings: SettingsDep) -> Lease:
    filer_url = None if settings.seaweed_url is None else str(settings.seaweed_url)
    return agent.heartbeat(body.capacity, filer_url, settings.recording_limit_bytes)


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
    body: TransitionIn,
    agent: LeasedDep,
    session: SessionDep,
    settings: SettingsDep,
    deployment_id: uuid.UUID,
) -> TransitionOut:
    deployment = _held(session, agent, deployment_id)
    deployment.transition(
        body.event,
        body.expected,
        body.reason,
        actor=Actor.AGENT,
        agent_id=agent.id,
        filer_url=None if settings.seaweed_url is None else str(settings.seaweed_url),
        limit_bytes=settings.recording_limit_bytes,
    )
    return TransitionOut(id=deployment.id, status=deployment.status)


@router.get("/{agent_id}/deployments/{deployment_id}/seed")
def seed(
    agent: LeasedDep, session: SessionDep, settings: SettingsDep, deployment_id: uuid.UUID
) -> SeedOut:
    deployment = _held(session, agent, deployment_id)
    if deployment.status is not DeploymentStatus.STARTING:
        raise ConflictError(f"deployment {deployment_id} is {deployment.status}, not STARTING")
    rendered = deployment.seed(
        stored_bundle(deployment, settings).files,
        settings.default_trainee_user,
        settings.default_trainee_sudo,
    )
    return SeedOut(user_data=rendered.user_data, meta_data=rendered.meta_data)


@router.get("/{agent_id}/deployments/{deployment_id}/checks/{check_id}")
def script(
    agent: LeasedDep,
    session: SessionDep,
    settings: SettingsDep,
    deployment_id: uuid.UUID,
    check_id: uuid.UUID,
) -> ScriptOut:
    deployment, _ = _pending(session, agent, deployment_id, check_id)
    bundle = stored_bundle(deployment, settings)
    # `from None`: the decoding error quotes the script's bytes, which are never logged.
    try:
        text = bundle.files[bundle.document["check"]].decode()
    except UnicodeDecodeError:
        raise RuntimeError(
            f"the check script of scenario {deployment.scenario_id} is not text"
        ) from None
    return ScriptOut(script=text, timeout_seconds=deployment.check_seconds)


@router.post("/{agent_id}/deployments/{deployment_id}/checks/{check_id}")
def result(
    body: ResultIn,
    agent: LeasedDep,
    session: SessionDep,
    mark: MarkDep,
    deployment_id: uuid.UUID,
    check_id: uuid.UUID,
) -> ResultOut:
    _, check = _pending(session, agent, deployment_id, check_id)
    if not check.settle(CheckStatus(body.status), body.exit_code, body.message, body.detail):
        raise ConflictError(f"check {check_id} is {check.status}")
    mark(check)
    return ResultOut(id=check.id, status=check.status)


@router.get("", dependencies=[Depends(require_operator)])
def index(session: SessionDep) -> AgentsOut:
    agents = sorted(Agent._load(session), key=lambda row: row.name)
    return AgentsOut(
        registrations=Agent(session=session).registrations(),
        agents=[agent_out(agent) for agent in agents],
    )


@router.post("/{agent_id}/state", dependencies=[Depends(require_operator)])
def switch(
    body: StateIn, session: SessionDep, settings: SettingsDep, agent_id: uuid.UUID
) -> AgentOut:
    """Enable or disable a stored agent; `enabled` on a pending registration approves it."""
    try:
        agent = Agent(session=session).load(id=agent_id)
    except IndexError:
        approved = None
        if body.state is State.ENABLED:
            approved = Agent(session=session, id=agent_id).approve()
        if approved is None:
            raise NotFoundError(f"agent {agent_id} is not known") from None
        return agent_out(approved)
    filer_url = None if settings.seaweed_url is None else str(settings.seaweed_url)
    return agent_out(agent.switch(body.state, filer_url, settings.recording_limit_bytes))
