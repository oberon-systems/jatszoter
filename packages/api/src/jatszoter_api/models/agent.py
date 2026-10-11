import hashlib
import secrets
import time
import uuid
from typing import Any, Self

from pydantic import PrivateAttr
from sqlmodel import JSON, Column, Field, Session, SQLModel

from jatszoter_api.errors import InvalidTransitionError
from jatszoter_api.lifecycle import TERMINAL, Actor, DeploymentStatus
from jatszoter_api.models._base import State
from jatszoter_api.models._cache import Cached
from jatszoter_api.models.deployment import Deployment
from jatszoter_api.models.secret import SecretOwner

LEASE_SECONDS = 30
CLAIM_SECONDS = 24 * 60 * 60
RUNNING = (
    DeploymentStatus.STARTING,
    DeploymentStatus.BOOTSTRAPPING,
    DeploymentStatus.READY,
    DeploymentStatus.STOPPING,
)


class Lease(SQLModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    capacity: int
    heartbeat_at: int
    expires_at: int


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


class Agent(SecretOwner, table=True):
    __tablename__ = "agents"

    name: str
    labels: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    _deployments: list[Deployment] | None = PrivateAttr(default=None)

    def __init__(self, session: Session, **data: Any) -> None:
        super().__init__(session, **data)

    @property
    def lease(self) -> Lease | None:
        """The live lease, read without renewing it; only a heartbeat does that."""
        lease: Lease | None = Cached._get(self, f"lease:{self.id}")
        return lease

    @property
    def deployments(self) -> list[Deployment]:
        if not self._deployments:
            self._deployments = Deployment._load(self._session, agent_id=self.id)
        return self._deployments

    def heartbeat(self, capacity: int, filer_url: str | None = None) -> Lease:
        """Renew the lease, failing what it held once it lapsed; then take waiting deployments,
        pinned ones first, oldest first, while `capacity` has room."""
        current = self.lease
        if current is None:
            for deployment in self.deployments:
                if deployment.status in RUNNING:
                    deployment.transition(
                        "abort",
                        deployment.status,
                        "lease expired",
                        actor=Actor.API,
                        filer_url=filer_url,
                    )
        now = int(time.time())
        lease = Lease(
            id=current.id if current else uuid.uuid4(),
            capacity=capacity,
            heartbeat_at=now,
            expires_at=now + LEASE_SECONDS,
        )
        self._set("lease", lease, LEASE_SECONDS)

        held = [row for row in self.deployments if row.status not in TERMINAL and row.vm_id]
        pinned = [
            row
            for row in self.deployments
            if row.status is DeploymentStatus.PENDING and row.vm_id is None
        ]
        waiting = Deployment._load(self._session, status=DeploymentStatus.PENDING, agent_id=None)
        free = capacity - len(held)
        for deployment in [
            *sorted(pinned, key=lambda row: row.created_at),
            *sorted(waiting, key=lambda row: row.created_at),
        ]:
            if free <= 0:
                break
            if deployment.assign(self.id):
                free -= 1
        self._deployments = None
        return lease

    def desired(self) -> list[dict[str, Any]]:
        """What the agent should run: every deployment it holds, with what its VM needs."""
        return [
            {
                "id": row.id,
                "vm_id": row.vm_id,
                "status": row.status,
                "scenario": {
                    "id": row.scenario.id,
                    "slug": row.scenario.slug,
                    "version": row.scenario.version,
                },
                "image": {"name": row.image.name, "url": row.image.url, "digest": row.image.digest},
                "resources": {
                    "cpus": row.cpus,
                    "memory_mib": row.memory_mib,
                    "disk_gib": row.disk_gib,
                },
                "timeouts": {
                    "bootstrap_seconds": row.bootstrap_seconds,
                    "check_seconds": row.check_seconds,
                    "session_minutes": row.session_minutes,
                },
            }
            for row in self.deployments
            if row.status not in TERMINAL and row.vm_id
        ]

    def claim_code(self, auto_register: bool) -> str:
        """Queue the unsaved agent for an operator, or enable it at once when agents register
        themselves; returns its one-time code."""
        code = secrets.token_urlsafe(32)
        if auto_register:
            self.state = State.ENABLED
            self.save()
        else:
            pending = {"name": self.name, "labels": self.labels, "state": self.state}
            self._set("registration", pending, CLAIM_SECONDS)
        Cached._set(self, f"claim:{_digest(code)}", self.id, CLAIM_SECONDS)
        return code

    def approve(self) -> Self | None:
        """Enable this agent waiting in the cache; None once it expired or was taken."""
        pending: dict[str, Any] | None = Cached()._get(f"registration:{self.id}")
        if pending is None or not Cached()._delete(f"registration:{self.id}"):
            return None
        self.name, self.labels = pending["name"], pending["labels"]
        self.state = State.ENABLED
        self.save()
        return self

    def claimed(self, code: str) -> Self | None:
        """The approved agent the code names; None for an unknown or spent code."""
        agent_id: uuid.UUID | None = Cached()._get(f"claim:{_digest(code)}")
        if agent_id is None:
            return None
        if Cached()._get(f"registration:{agent_id}") is not None:
            raise InvalidTransitionError(f"agent {agent_id} waits for an operator")
        return self.load(id=agent_id)

    def redeem(self, code: str) -> bool:
        """Spend the claim code; only the first of two racing claims gets True."""
        return Cached._delete(self, f"claim:{_digest(code)}")
