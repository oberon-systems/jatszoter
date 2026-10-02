from jatszoter_api.routes.agents import router as agents_router
from jatszoter_api.routes.auth import router as auth_router
from jatszoter_api.routes.deployments import router as deployments_router
from jatszoter_api.routes.deps import domain_error_handler
from jatszoter_api.routes.images import router as images_router
from jatszoter_api.routes.scenarios import router as scenarios_router

__all__ = [
    "agents_router",
    "auth_router",
    "deployments_router",
    "domain_error_handler",
    "images_router",
    "scenarios_router",
]
