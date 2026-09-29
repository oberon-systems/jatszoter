from typing import Any

from sqlmodel import JSON, Column, Field, UniqueConstraint

from jatszoter_api.models._base import Base


class Scenario(Base, table=True):
    __tablename__ = "scenarios"
    __table_args__ = (UniqueConstraint("slug", "version"),)

    slug: str
    version: int
    document: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    files: dict[str, str] = Field(sa_column=Column(JSON, nullable=False))
