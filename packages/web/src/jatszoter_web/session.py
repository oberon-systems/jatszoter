import time
from typing import Annotated, Any
from urllib.parse import quote, urlsplit

import httpx
from fastapi import Depends, Request

from jatszoter_web.api import Api, failed
from jatszoter_web.cookies import SESSION_COOKIE, Operator, seal_operator, unseal_operator
from jatszoter_web.csrf import check
from jatszoter_web.middleware import queue_cookie
from jatszoter_web.settings import Settings

REFRESH_SECONDS = 15 * 60


class SignedOut(Exception):
    """No live session: none, one that does not decrypt, a lapsed one, or one the API refused."""

    def __init__(self, refused: bool = False) -> None:
        super().__init__("signed out")
        self.refused = refused


class NotAllowed(Exception):
    """The API answered 403 to the operator."""


def local(path: str | None) -> str | None:
    """`path` when it names a page of this host, else None."""
    if not path:
        return None
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or not path.startswith("/") or path.startswith(("//", "/\\")):
        return None
    return path


def login_url(request: Request) -> str:
    """`/login`, with `next` naming the page the browser was on when it is a local GET."""
    page: str | None = None
    if "hx-request" in request.headers:
        current = urlsplit(request.headers.get("hx-current-url", ""))
        if current.netloc.lower() == request.headers.get("host", "").lower():
            page = current.path + (f"?{current.query}" if current.query else "")
    elif request.method == "GET":
        page = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    page = local(page)
    return "/login" if page is None else f"/login?next={quote(page, safe='/')}"


class Session:
    """The operator behind a request's session cookie, and the API calls signed on their behalf."""

    def __init__(self, request: Request, operator: Operator) -> None:
        self.request = request
        self.operator = operator
        self.api: Api = request.app.state.api
        self.settings: Settings = request.app.state.settings

    async def call(
        self,
        method: str,
        path: str,
        payload: Any = None,
        params: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes]] | None = None,
    ) -> httpx.Response:
        """A signed call; 401, 403 and 5xx are raised, any other answer is the caller's to show."""
        now = int(time.time())
        if self.operator.expires_at <= now:
            raise SignedOut()
        if self.operator.expires_at - now <= REFRESH_SECONDS:
            await self.refresh()
        answer = await self.api.signed(self.operator, method, path, payload, params, files)
        return self.checked(answer)

    async def refresh(self) -> None:
        response = self.checked(
            await self.api.signed(self.operator, "POST", "/api/v1/auth/credential")
        )
        try:
            fresh = response.json()
            self.operator = self.operator.model_copy(
                update={
                    "credential": fresh["credential"],
                    "expires_at": int(fresh["expires_at"]),
                    "key": fresh["key"],
                }
            )
        except (ValueError, KeyError, TypeError):
            raise failed(response.status_code) from None
        self.request.state.operator = self.operator
        sealed = seal_operator(self.operator, self.request.app.state.keys, self.settings.cookie_kid)
        queue_cookie(self.request, SESSION_COOKIE, sealed)

    @staticmethod
    def checked(response: httpx.Response) -> httpx.Response:
        if response.status_code == 401:
            raise SignedOut(refused=True)
        if response.status_code == 403:
            raise NotAllowed()
        if response.status_code >= 500:
            raise failed(response.status_code)
        return response


def current_operator(request: Request) -> Session:
    operator = unseal_operator(request.cookies.get(SESSION_COOKIE), request.app.state.keys)
    if operator is None or operator.expires_at <= int(time.time()):
        raise SignedOut()
    request.state.operator = operator
    return Session(request, operator)


async def posted_operator(request: Request) -> Session:
    """The operator of a state-changing request, once its CSRF token checks out."""
    session = current_operator(request)
    await check(request, session.operator.csrf)
    return session


OperatorDep = Annotated[Session, Depends(current_operator)]
PostedOperatorDep = Annotated[Session, Depends(posted_operator)]
