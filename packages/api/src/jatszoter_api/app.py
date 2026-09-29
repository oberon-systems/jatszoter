from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import APIRouter, FastAPI
from sqlmodel import create_engine

from jatszoter_api.errors import DomainError
from jatszoter_api.routes import agents_router, domain_error_handler
from jatszoter_api.settings import get_settings

MIGRATIONS = Path(__file__).parent / "migrations"


def migrate(dsn: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    # The option goes through ConfigParser interpolation, so a percent-encoded DSN needs escaping.
    config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
    command.upgrade(config, "head")


def create_app() -> FastAPI:
    dsn = str(get_settings().database_dsn)
    migrate(dsn)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = create_engine(dsn)
    app.add_exception_handler(DomainError, domain_error_handler)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    v1 = APIRouter(prefix="/api/v1")
    v1.include_router(agents_router)
    app.include_router(v1)
    return app
