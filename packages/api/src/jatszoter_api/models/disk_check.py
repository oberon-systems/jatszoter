import uuid
from typing import Any, ClassVar, Self

from sqlmodel import Field, Session

from jatszoter_api.models._base import Base
from jatszoter_api.models.check import MESSAGE_CHARS, CheckStatus


class DiskCheck(Base, table=True):
    __tablename__ = "disk_checks"

    CONFLICT: ClassVar[str] = "disk-check-settled"
    deployment_id: uuid.UUID = Field(unique=True)
    status: CheckStatus
    message: str | None = None

    def __init__(self, session: Session, **data: Any) -> None:
        lines = (data.pop("message", None) or "").splitlines()
        super().__init__(session, message=lines[0][:MESSAGE_CHARS] if lines else None, **data)

    def load(self, **key: Any) -> Self:
        return self._load(self._session, **key)[0]

    def save(self) -> None:
        self._save()

    @property
    def marker(self) -> str:
        """What the verdict writes into the recording."""
        head = f"disk check {self.status}"
        return f"{head}: {self.message}" if self.message else head
