from functools import cache
from typing import Annotated

from pydantic import AnyHttpUrl, AnyUrl, UrlConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict

ValkeyDsn = Annotated[
    AnyUrl, UrlConstraints(allowed_schemes=["redis", "rediss", "valkey", "valkeys", "unix"])
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JATSZOTER_")

    database_dsn: AnyUrl
    valkey_dsn: ValkeyDsn | None = None
    seaweed_url: AnyHttpUrl | None = None
    master_keys: dict[str, str] = {}
    master_kid: str = ""
    auto_register_agents: bool = False


@cache
def get_settings() -> Settings:
    return Settings()
