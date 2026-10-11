import logging
import time
import uuid
from typing import Any, Self

from pydantic import PrivateAttr
from sqlmodel import Field, Session

from jatszoter_api.errors import InvalidTransitionError
from jatszoter_api.lifecycle import TERMINAL, Actor, DeploymentStatus, advance
from jatszoter_api.models._base import Base
from jatszoter_api.models.check import Check
from jatszoter_api.models.console import Console
from jatszoter_api.models.image import Image
from jatszoter_api.models.link import Link
from jatszoter_api.models.scenario import Scenario
from jatszoter_api.seed import Seed, render

log = logging.getLogger(__name__)


class Deployment(Base, table=True):
    __tablename__ = "deployments"

    scenario_id: uuid.UUID = Field(index=True)
    image_id: uuid.UUID = Field(index=True)
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
    _scenario: Scenario | None = PrivateAttr(default=None)
    _image: Image | None = PrivateAttr(default=None)
    _links: list[Link] | None = PrivateAttr(default=None)
    _checks: list[Check] | None = PrivateAttr(default=None)
    _console: Console | None = PrivateAttr(default=None)

    def __init__(self, session: Session, **data: Any) -> None:
        super().__init__(session, **data)

    def load(self, **key: Any) -> Self:
        return self._load(self._session, **key)[0]

    def save(self) -> None:
        self._save()

    def stop(self, filer_url: str | None = None) -> None:
        """Cancel a PENDING deployment, stop a running one; a repeated stop changes nothing."""
        if self.status in (DeploymentStatus.STOPPING, DeploymentStatus.CANCELLED):
            return
        event = "cancel" if self.status is DeploymentStatus.PENDING else "stop"
        self.transition(event, self.status, actor=Actor.OPERATOR, filer_url=filer_url)

    @property
    def scenario(self) -> Scenario:
        if not self._scenario:
            self._scenario = Scenario(session=self._session).load(id=self.scenario_id)
        return self._scenario

    @property
    def image(self) -> Image:
        if not self._image:
            self._image = Image(session=self._session).load(id=self.image_id)
        return self._image

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

    @property
    def console(self) -> Console:
        if not self._console:
            self._console = Console(self.id)
        return self._console

    def assign(self, agent_id: uuid.UUID) -> bool:
        """Take this PENDING deployment for `agent_id`; False once someone else got it first."""
        self._session.refresh(self, with_for_update=True)
        if self.status is not DeploymentStatus.PENDING or self.agent_id not in (None, agent_id):
            return False
        self.agent_id = agent_id
        if self.vm_id is None:
            self.vm_id = uuid.uuid4()
        self.updated_at = int(time.time())
        self.save()
        return True

    def seed(self, files: dict[str, bytes], trainee_user: str, trainee_sudo: bool) -> Seed:
        """The cloud-init seed of this deployment, from its scenario bundle's `files`."""
        if self.vm_id is None:
            raise ValueError(f"deployment {self.id} has no VM yet")
        return render(self.scenario.document, files, self.vm_id, trainee_user, trainee_sudo)

    def transition(
        self,
        event: str,
        expected: DeploymentStatus,
        reason: str | None = None,
        *,
        actor: Actor,
        agent_id: uuid.UUID | None = None,
        filer_url: str | None = None,
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
        if target is DeploymentStatus.READY:
            self.console.mark_ready(filer_url)
        if target in TERMINAL:
            self.console.drop()
            for link in self.links:
                # The deployment has ended either way: a recording left open never undoes that.
                try:
                    link.recording.close(filer_url)
                except IndexError:
                    continue
                except Exception as error:
                    log.error(
                        "recording of deployment %s not closed: %s", self.id, type(error).__name__
                    )
