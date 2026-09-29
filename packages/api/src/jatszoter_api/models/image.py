from sqlmodel import Field

from jatszoter_api.models._base import Base


class Image(Base, table=True):
    __tablename__ = "images"

    name: str = Field(unique=True)
    url: str
    digest: str
