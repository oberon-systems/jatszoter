import uuid
from typing import cast

import httpx
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from jatszoter_api.auth import require_operator
from jatszoter_api.errors import ConflictError, InvalidReferenceError, NotFoundError
from jatszoter_api.file import File
from jatszoter_api.lifecycle import TERMINAL, DeploymentStatus
from jatszoter_api.models import (
    Agent,
    Deployment,
    Link,
    Recording,
    Role,
    Scenario,
    State,
    User,
)
from jatszoter_api.routes.agents import StrictModel
from jatszoter_api.routes.deps import SessionDep, SettingsDep
from jatszoter_api.routes.links import LinkOut, link_out


class DeploymentIn(StrictModel):
    scenario_id: uuid.UUID
    trainee_id: uuid.UUID
    agent_id: uuid.UUID | None = None


class LinkIn(StrictModel):
    user_id: uuid.UUID


class DeploymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scenario_id: uuid.UUID
    image_id: uuid.UUID
    agent_id: uuid.UUID | None
    vm_id: uuid.UUID | None
    status: DeploymentStatus
    reason: str | None
    cpus: int
    memory_mib: int
    disk_gib: int
    bootstrap_seconds: int
    check_seconds: int
    session_minutes: int
    created_at: int
    updated_at: int
    ready_at: int | None
    ended_at: int | None


class RecordingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    link_id: uuid.UUID
    created_at: int
    size_bytes: int
    duration_seconds: float


class TraineeLink(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    token: str


class DeploymentCreated(DeploymentOut):
    link: TraineeLink


class LinkIssued(LinkOut):
    token: str


def _deployment(session: SessionDep, deployment_id: uuid.UUID) -> Deployment:
    try:
        return Deployment(session=session).load(id=deployment_id)
    except IndexError:
        raise NotFoundError(f"deployment {deployment_id} is not known") from None


def _owner(session: SessionDep, user_id: uuid.UUID) -> User:
    """An enabled user to own a link; any other is refused like an unusable agent."""
    try:
        user = User(session=session).load(id=user_id)
    except IndexError:
        user = None
    if user is None or user.state is not State.ENABLED:
        raise InvalidReferenceError(f"user {user_id} is not an enabled user")
    return user


def _holds_trainee(session: SessionDep, deployment: Deployment) -> bool:
    return any(
        link.revoked_at is None and User(session=session).load(id=link.user_id).role is Role.TRAINEE
        for link in deployment.links
    )


router = APIRouter(prefix="/deployments", dependencies=[Depends(require_operator)])


@router.post("", status_code=201)
def create(body: DeploymentIn, session: SessionDep, settings: SettingsDep) -> DeploymentCreated:
    try:
        scenario = Scenario(session=session).load(id=body.scenario_id)
    except IndexError:
        raise NotFoundError(f"scenario {body.scenario_id} is not known") from None
    try:
        image = scenario.image
    except IndexError:
        raise ConflictError(f"image {scenario.document['image']} is not registered") from None
    if body.agent_id is not None:
        try:
            agent = Agent(session=session).load(id=body.agent_id)
        except IndexError:
            agent = None
        if agent is None or agent.state is not State.ENABLED:
            raise InvalidReferenceError(f"agent {body.agent_id} is not an enabled agent")
    trainee = _owner(session, body.trainee_id)
    if trainee.role is not Role.TRAINEE:
        raise InvalidReferenceError(f"user {body.trainee_id} is not a trainee")
    resources = scenario.document.get("resources", {})
    timeouts = scenario.document.get("timeouts", {})
    deployment = Deployment(
        session=session,
        scenario_id=scenario.id,
        image_id=image.id,
        agent_id=body.agent_id,
        cpus=resources.get("cpus", settings.default_cpus),
        memory_mib=resources.get("memory_mib", settings.default_memory_mib),
        disk_gib=resources.get("disk_gib", settings.default_disk_gib),
        bootstrap_seconds=timeouts.get("bootstrap_seconds", settings.default_bootstrap_seconds),
        check_seconds=timeouts.get("check_seconds", settings.default_check_seconds),
        session_minutes=timeouts.get("session_minutes", settings.default_session_minutes),
    )
    deployment.save()
    link = Link(session=session, deployment_id=deployment.id, user_id=trainee.id)
    try:
        link.save()
    except Exception:
        # Without its link the deployment must not wait for an agent.
        session.rollback()
        deployment.stop(None if settings.seaweed_url is None else str(settings.seaweed_url))
        raise
    return DeploymentCreated(
        **DeploymentOut.model_validate(deployment).model_dump(),
        link=TraineeLink(id=link.id, user_id=link.user_id, token=cast(str, link.token)),
    )


@router.get("")
def index(session: SessionDep, status: DeploymentStatus | None = None) -> list[DeploymentOut]:
    key = {} if status is None else {"status": status}
    deployments = sorted(
        Deployment._load(session, **key), key=lambda row: row.created_at, reverse=True
    )
    return [DeploymentOut.model_validate(deployment) for deployment in deployments]


@router.get("/{deployment_id}")
def show(session: SessionDep, deployment_id: uuid.UUID) -> DeploymentOut:
    return DeploymentOut.model_validate(_deployment(session, deployment_id))


@router.post("/{deployment_id}/stop")
def stop(session: SessionDep, settings: SettingsDep, deployment_id: uuid.UUID) -> DeploymentOut:
    deployment = _deployment(session, deployment_id)
    deployment.stop(None if settings.seaweed_url is None else str(settings.seaweed_url))
    return DeploymentOut.model_validate(deployment)


@router.post("/{deployment_id}/links", status_code=201)
def issue(body: LinkIn, session: SessionDep, deployment_id: uuid.UUID) -> LinkIssued:
    deployment = _deployment(session, deployment_id)
    if deployment.status in TERMINAL:
        raise ConflictError(f"deployment {deployment.id} is {deployment.status}")
    user = _owner(session, body.user_id)
    if user.role is Role.TRAINEE and _holds_trainee(session, deployment):
        raise ConflictError(f"deployment {deployment.id} holds a live trainee link")
    link = Link(session=session, deployment_id=deployment.id, user_id=user.id)
    link.save()
    return LinkIssued(**link_out(session, link).model_dump(), token=cast(str, link.token))


@router.get("/{deployment_id}/links")
def links(session: SessionDep, deployment_id: uuid.UUID) -> list[LinkOut]:
    deployment = _deployment(session, deployment_id)
    return [link_out(session, link) for link in deployment.links]


@router.get("/{deployment_id}/recordings")
def recordings(session: SessionDep, deployment_id: uuid.UUID) -> list[RecordingOut]:
    deployment = _deployment(session, deployment_id)
    stored = Recording._load(session, deployment_id=deployment.id)
    return [
        RecordingOut.model_validate(recording)
        for recording in sorted(stored, key=lambda row: row.created_at)
    ]


@router.get("/{deployment_id}/console")
def console(session: SessionDep, settings: SettingsDep, deployment_id: uuid.UUID) -> Response:
    deployment = _deployment(session, deployment_id)
    filer_url = None if settings.seaweed_url is None else str(settings.seaweed_url)
    log = File(deployment.console.path, filer_url=filer_url)
    try:
        log.get()
    except httpx.HTTPStatusError as error:
        if error.response.status_code != 404:
            raise
    return Response(log.data, media_type="application/octet-stream")
