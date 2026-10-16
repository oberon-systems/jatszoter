import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from jatszoter_web import (
    agents,
    catalog,
    deployments,
    detail,
    logs,
    replay,
    results,
    storage,
    trainee,
    users,
)
from jatszoter_web.api import Api
from jatszoter_web.auth import router as auth_router
from jatszoter_web.middleware import DropUvicornAccess, Guard
from jatszoter_web.pages import HANDLERS
from jatszoter_web.relay import router as relay_router
from jatszoter_web.settings import Settings

STATIC = Path(__file__).parent / "static"

_DROP_UVICORN_ACCESS = DropUvicornAccess()


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    """The app; `transport` replaces the network under the API client, for tests."""
    settings = settings or Settings()
    api = Api(settings.api_base, transport)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await api.aclose()

    # The same filter instance every time, so a second app adds nothing.
    logging.getLogger("uvicorn.access").addFilter(_DROP_UVICORN_ACCESS)
    app = FastAPI(
        docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.keys = settings.aes_keys()
    app.state.api = api
    for error, handler in HANDLERS.items():
        app.add_exception_handler(error, handler)
    app.add_middleware(Guard)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router)
    screens = (deployments, detail, replay, catalog, agents, users, logs, storage, results, trainee)
    for screen in screens:
        app.include_router(screen.router)
    app.include_router(relay_router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
