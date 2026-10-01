import hashlib
import secrets
import time
import uuid
from typing import Any, Self

from sqlmodel import JSON, Column, Field, Session, SQLModel

from jatszoter_api.errors import InvalidTransitionError
from jatszoter_api.lifecycle import Actor, DeploymentStatus
from jatszoter_api.models._base import State
from jatszoter_api.models._cache import Cached
from jatszoter_api.models.deployment import Deployment
from jatszoter_api.models.secret import SecretOwner
from jatszoter_api.settings import get_settings

LEASE_SECONDS = 30
CLAIM_SECONDS = 24 * 60 * 60
HELD = (
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

    def __init__(self, session: Session, **data: Any) -> None:
        super().__init__(session, **data)

    @property
    def lease(self) -> Lease | None:
        """The live lease, read without renewing it; only a heartbeat does that."""
        lease: Lease | None = Cached._get(self, f"lease:{self.id}")
        return lease

    @property
    def deployments(self) -> list[Deployment]:
        return Deployment._load(self._session, agent_id=self.id)

    def heartbeat(self, capacity: int) -> Lease:
        """Renew the lease; once it lapsed, fail what it held before a new one opens."""
        current = self.lease
        if current is None:
            for deployment in self.deployments:
                if deployment.status in HELD:
                    deployment.transition(
                        "abort", deployment.status, "lease expired", actor=Actor.API
                    )
        now = int(time.time())
        lease = Lease(
            id=current.id if current else uuid.uuid4(),
            capacity=capacity,
            heartbeat_at=now,
            expires_at=now + LEASE_SECONDS,
        )
        self._set("lease", lease, LEASE_SECONDS)
        return lease

    def claim_code(self) -> str:
        """Queue the unsaved agent for an operator, or enable it at once when agents register
        themselves; returns its one-time code."""
        code = secrets.token_urlsafe(32)
        if get_settings().auto_register_agents:
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
