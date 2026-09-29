from sqlmodel import JSON, Column, Field

from jatszoter_api.models.secret import SecretOwner


class Agent(SecretOwner, table=True):
    __tablename__ = "agents"

    name: str
    labels: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
