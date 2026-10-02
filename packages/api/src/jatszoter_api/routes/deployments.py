import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from jatszoter_api.auth import require_operator
from jatszoter_api.errors import ConflictError, InvalidReferenceError, NotFoundError
from jatszoter_api.lifecycle import DeploymentStatus
from jatszoter_api.models import Agent, Deployment, Scenario, State
from jatszoter_api.routes.agents import StrictModel
from jatszoter_api.routes.deps import SessionDep, SettingsDep


class DeploymentIn(StrictModel):
    scenario_id: uuid.UUID
    agent_id: uuid.UUID | None = None


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


def _deployment(session: SessionDep, deployment_id: uuid.UUID) -> Deployment:
    try:
        return Deployment(session=session).load(id=deployment_id)
    except IndexError:
        raise NotFoundError(f"deployment {deployment_id} is not known") from None


router = APIRouter(prefix="/deployments", dependencies=[Depends(require_operator)])


@router.post("", status_code=201)
def create(body: DeploymentIn, session: SessionDep, settings: SettingsDep) -> DeploymentOut:
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
    return DeploymentOut.model_validate(deployment)


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
def stop(session: SessionDep, deployment_id: uuid.UUID) -> DeploymentOut:
    deployment = _deployment(session, deployment_id)
    deployment.stop()
    return DeploymentOut.model_validate(deployment)
