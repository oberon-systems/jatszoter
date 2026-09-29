import uuid

from sqlmodel import Field

from jatszoter_api.models._base import Base


class Recording(Base, table=True):
    __tablename__ = "recordings"

    link_id: uuid.UUID = Field(unique=True)
    url: str
    size_bytes: int = 0
    duration_seconds: float = 0.0
