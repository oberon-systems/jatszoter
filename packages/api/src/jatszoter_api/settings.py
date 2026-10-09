from typing import Annotated

from fastapi import Request
from pydantic import AnyHttpUrl, AnyUrl, Field, PositiveInt, UrlConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict

ValkeyDsn = Annotated[
    AnyUrl, UrlConstraints(allowed_schemes=["redis", "rediss", "valkey", "valkeys", "unix"])
]
UserName = Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JATSZOTER_")

    database_dsn: AnyUrl
    valkey_dsn: ValkeyDsn | None = None
    seaweed_url: AnyHttpUrl | None = None
    master_keys: dict[str, str] = {}
    master_kid: str = ""
    auto_register_agents: bool = False
    scenario_bundle_limit_bytes: int = 10 * 1024 * 1024
    console_tail_bytes: PositiveInt = 8 * 1024 * 1024
    console_limit_bytes: PositiveInt = 64 * 1024 * 1024
    console_viewer_buffer_bytes: PositiveInt = 4 * 1024 * 1024

    default_cpus: int = 1
    default_memory_mib: int = 1024
    default_disk_gib: int = 10
    default_bootstrap_seconds: int = 900
    default_check_seconds: int = 30
    default_session_minutes: int = 60
    default_trainee_user: UserName = "trainee"
    default_trainee_sudo: bool = False


def app_settings(request: Request) -> Settings:
    """The settings `create_app` was built with."""
    settings: Settings = request.app.state.settings
    return settings
