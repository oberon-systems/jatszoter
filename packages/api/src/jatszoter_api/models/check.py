import uuid
from enum import StrEnum

from sqlmodel import Field

from jatszoter_api.models._base import Base


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
    finished_at: int | None = None
