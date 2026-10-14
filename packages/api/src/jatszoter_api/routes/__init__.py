from jatszoter_api.routes.agents import router as agents_router
from jatszoter_api.routes.auth import router as auth_router
from jatszoter_api.routes.checks import all_router as all_checks_router
from jatszoter_api.routes.checks import router as checks_router
from jatszoter_api.routes.console import router as console_router
from jatszoter_api.routes.deployments import party_router as deployment_party_router
from jatszoter_api.routes.deployments import router as deployments_router
from jatszoter_api.routes.deployments import storage_router
from jatszoter_api.routes.deps import domain_error_handler
from jatszoter_api.routes.images import router as images_router
from jatszoter_api.routes.links import open_router
from jatszoter_api.routes.links import router as links_router
from jatszoter_api.routes.recordings import router as recordings_router
from jatszoter_api.routes.scenarios import router as scenarios_router
from jatszoter_api.routes.users import router as users_router

__all__ = [
    "agents_router",
    "all_checks_router",
    "auth_router",
    "checks_router",
    "console_router",
    "deployment_party_router",
    "deployments_router",
    "domain_error_handler",
    "images_router",
    "links_router",
    "open_router",
    "recordings_router",
    "scenarios_router",
    "storage_router",
    "users_router",
]
