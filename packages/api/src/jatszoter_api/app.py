import logging
import re
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
    all_checks_router,
    auth_router,
    checks_router,
    console_router,
    deployment_party_router,
    deployments_router,
    domain_error_handler,
    images_router,
    links_router,
    open_router,
    recordings_router,
    scenarios_router,
    storage_router,
    users_router,
)
from jatszoter_api.settings import Settings

MIGRATIONS = Path(__file__).parent / "migrations"

LINK_PATH = re.compile(r"/s/[^/?#\s]+")

log = logging.getLogger(__name__)


class RedactLinks(logging.Filter):
    """Rewrite `/s/<token>` in an access-log record, so no link token reaches the log."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                LINK_PATH.sub("/s/<redacted>", arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True


_REDACT_LINKS = RedactLinks()


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
    # The same filter instance every time, so a second app adds nothing.
    logging.getLogger("uvicorn.access").addFilter(_REDACT_LINKS)
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
    v1.include_router(all_checks_router)
    v1.include_router(auth_router)
    v1.include_router(checks_router)
    v1.include_router(console_router)
    v1.include_router(deployments_router)
    v1.include_router(deployment_party_router)
    v1.include_router(images_router)
    v1.include_router(links_router)
    v1.include_router(recordings_router)
    v1.include_router(scenarios_router)
    v1.include_router(storage_router)
    v1.include_router(users_router)
    app.include_router(v1)
    app.include_router(open_router)
    return app
