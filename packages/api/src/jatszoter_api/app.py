import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import APIRouter, FastAPI
from sqlmodel import Session, create_engine

from jatszoter_api.console_hub import ConsoleHub
from jatszoter_api.errors import DomainError
from jatszoter_api.models import Role, State, User
from jatszoter_api.models._cache import Cached
from jatszoter_api.models.user import ADMIN
from jatszoter_api.routes import (
    agents_router,
    auth_router,
    console_router,
    deployments_router,
    domain_error_handler,
    images_router,
    scenarios_router,
)
from jatszoter_api.settings import Settings

MIGRATIONS = Path(__file__).parent / "migrations"

log = logging.getLogger(__name__)


def migrate(dsn: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    # The option goes through ConfigParser interpolation, so a percent-encoded DSN needs escaping.
    config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
    command.upgrade(config, "head")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    dsn = str(settings.database_dsn)
    migrate(dsn)
    Cached.connect(None if settings.valkey_dsn is None else str(settings.valkey_dsn))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.engine = create_engine(dsn)
    app.state.console_hub = ConsoleHub(settings.console_viewer_buffer_bytes)
    with Session(app.state.engine) as session:
        try:
            User(session=session).load(role=Role.OPERATOR)
        except IndexError:
            admin = User(
                session=session,
                name=ADMIN,
                role=Role.OPERATOR,
                state=State.ENABLED,
                password=ADMIN,
            )
            admin.save()
            log.warning("no operator existed: created user admin, due for a password change")
    app.add_exception_handler(DomainError, domain_error_handler)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    v1 = APIRouter(prefix="/api/v1")
    v1.include_router(agents_router)
    v1.include_router(auth_router)
    v1.include_router(console_router)
    v1.include_router(deployments_router)
    v1.include_router(images_router)
    v1.include_router(scenarios_router)
    app.include_router(v1)
    return app
