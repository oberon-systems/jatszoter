import time
import uuid
from enum import StrEnum
from typing import Any, Self

from sqlmodel import Field, Session

from jatszoter_api.models._base import Base

GRACE_SECONDS = 30
MESSAGE_CHARS = 200
DETAIL_BYTES = 4096


class CheckStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    TIMEOUT = "timeout"


class Check(Base, table=True):
    __tablename__ = "checks"

    deployment_id: uuid.UUID = Field(index=True)
    link_id: uuid.UUID | None = None
    user_id: uuid.UUID
    status: CheckStatus = CheckStatus.PENDING
    exit_code: int | None = None
    message: str | None = None
    detail: str | None = None
    finished_at: int | None = None

    def __init__(self, session: Session, **data: Any) -> None:
        super().__init__(session, **data)

    def load(self, **key: Any) -> Self:
        return self._load(self._session, **key)[0]

    def save(self) -> None:
        self._save()

    @property
    def marker(self) -> str:
        """What a settled check writes into the recording."""
        return f"check {self.status}: {self.message}" if self.message else f"check {self.status}"

    def settle(
        self,
        status: CheckStatus,
        exit_code: int | None = None,
        message: str | None = None,
        detail: str | None = None,
    ) -> bool:
        """Store the result of a pending check; False once it was settled before."""
        self._session.refresh(self, with_for_update=True)
        if self.status is not CheckStatus.PENDING:
            return False
        lines = (message or "").splitlines()
        self.status = status
        self.exit_code = exit_code
        self.message = lines[0][:MESSAGE_CHARS] if lines else None
        # Cut on a character boundary: a split sequence at the end is dropped.
        self.detail = (detail or "").encode()[:DETAIL_BYTES].decode(errors="ignore") or None
        self.finished_at = int(time.time())
        self.save()
        return True

    def expire(self, check_seconds: int) -> bool:
        """Settle a pending check nobody answered in time as `timeout`; True when it did."""
        overdue = time.time() - self.created_at > check_seconds + GRACE_SECONDS
        if self.status is not CheckStatus.PENDING or not overdue:
            return False
        return self.settle(CheckStatus.TIMEOUT)
