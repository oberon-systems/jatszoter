import uuid

from fastapi import APIRouter, Request
from fastapi.responses import Response

from jatszoter_web.csrf import browser_token
from jatszoter_web.pages import render

router = APIRouter()


@router.get("/t/{deployment_id:uuid}")
def trainee(request: Request, deployment_id: uuid.UUID) -> Response:
    """The trainee page: a shell its script fills from the relayed routes with the link cookie."""
    context = {"id": deployment_id, "csrf_token": browser_token(request)}
    return render(request, "trainee.html", context)
