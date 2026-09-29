import time
import uuid
from typing import Any, Self

from sqlmodel import Field, col, select, update

from jatszoter_api.errors import InvalidTransitionError
from jatszoter_api.lifecycle import TERMINAL, Actor, DeploymentStatus, advance
from jatszoter_api.models._base import Base, session_of
from jatszoter_api.models.check import Check
from jatszoter_api.models.link import Link
from jatszoter_api.models.user import Role, User


class Deployment(Base, table=True):
    __tablename__ = "deployments"

    scenario_id: uuid.UUID = Field(index=True)
    agent_id: uuid.UUID | None = Field(default=None, index=True)
    vm_id: uuid.UUID | None = None
    status: DeploymentStatus = Field(default=DeploymentStatus.PENDING, index=True)
    reason: str | None = None
    cpus: int
    memory_mib: int
    disk_gib: int
    bootstrap_seconds: int
    check_seconds: int
    session_minutes: int
    updated_at: int = Field(default_factory=lambda: int(time.time()))
    ready_at: int | None = None
    ended_at: int | None = None

    @property
    def links(self) -> list[Link]:
        statement = select(Link).where(col(Link.deployment_id) == self.id)
        return list(session_of(self).exec(statement))

    @property
    def checks(self) -> list[Check]:
        statement = select(Check).where(col(Check.deployment_id) == self.id)
        return list(session_of(self).exec(statement))

    @classmethod
    def register(cls, trainee: User, **fields: Any) -> tuple[Self, str]:
        """Store a deployment with its trainee link; returns the link's plain token."""
        if trainee.role is not Role.TRAINEE:
            raise ValueError("a deployment is registered together with its trainee")
        deployment = cls(**fields)
        _, token = Link.issue(deployment.id, trainee.id)
        session_of(deployment).commit()
        return deployment, token

    def transition(
        self,
        event: str,
        expected: DeploymentStatus,
        reason: str | None = None,
        *,
        actor: Actor,
        agent_id: uuid.UUID | None = None,
    ) -> None:
        target = advance(expected, event, actor)
        if target is DeploymentStatus.FAILED and not reason:
            raise ValueError("FAILED requires a reason")
        if actor is Actor.AGENT and agent_id is None:
            raise ValueError("an agent transition names its agent")

        session = session_of(self)
        now = int(time.time())
        statement = update(Deployment).where(
            col(Deployment.id) == self.id, col(Deployment.status) == expected
        )
        if agent_id is not None:
            statement = statement.where(col(Deployment.agent_id) == agent_id)
        statement = statement.values(
            status=target, reason=reason, updated_at=now, **self._stamps(target, now)
        )
        result = session.exec(statement)
        if result.rowcount == 1 and target in TERMINAL:
            for link in self.links:
                link.revoked_at = link.revoked_at or now
        session.commit()

        session.refresh(self)
        if result.rowcount == 1:
            return
        if self.status is target and agent_id in (None, self.agent_id):
            return
        raise InvalidTransitionError(f"deployment {self.id} is {self.status}, expected {expected}")

    @staticmethod
    def _stamps(target: DeploymentStatus, now: int) -> dict[str, int]:
        if target is DeploymentStatus.READY:
            return {"ready_at": now}
        return {"ended_at": now} if target in TERMINAL else {}
