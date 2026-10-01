import time
import uuid
from typing import Any, Self

from pydantic import PrivateAttr
from sqlmodel import Field

from jatszoter_api.errors import InvalidTransitionError
from jatszoter_api.lifecycle import TERMINAL, Actor, DeploymentStatus, advance
from jatszoter_api.models._base import Base
from jatszoter_api.models.check import Check
from jatszoter_api.models.link import Link


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
    _links: list[Link] | None = PrivateAttr(default=None)
    _checks: list[Check] | None = PrivateAttr(default=None)

    def load(self, **key: Any) -> Self:
        return self._load(self._session, **key)[0]

    def save(self) -> None:
        self._save()

    @property
    def links(self) -> list[Link]:
        if not self._links:
            self._links = Link._load(self._session, deployment_id=self.id)
        return self._links

    @property
    def checks(self) -> list[Check]:
        if not self._checks:
            self._checks = Check._load(self._session, deployment_id=self.id)
        return self._checks

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

        self._session.refresh(self, with_for_update=True)
        owned = agent_id in (None, self.agent_id)
        if self.status is not expected or not owned:
            if self.status is target and owned:
                return
            raise InvalidTransitionError(
                f"deployment {self.id} is {self.status}, expected {expected}"
            )
        now = int(time.time())
        self.status = target
        self.reason = reason
        self.updated_at = now
        if target is DeploymentStatus.READY:
            self.ready_at = now
        if target in TERMINAL:
            self.ended_at = now
            for link in self.links:
                if link.revoked_at is None:
                    link.revoked_at = now
        self.save()
