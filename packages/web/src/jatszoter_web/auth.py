import time
import uuid
from contextlib import suppress
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, ValidationError
from starlette.datastructures import FormData

from jatszoter_web.api import Api, ApiDown, failed, message
from jatszoter_web.cookies import (
    SESSION_COOKIE,
    Operator,
    new_token,
    seal,
    seal_operator,
    unseal,
)
from jatszoter_web.csrf import browser_token, check_browser
from jatszoter_web.middleware import queue_cookie
from jatszoter_web.pages import LANDING, Flash, error, refusal, render, templates
from jatszoter_web.session import OperatorDep, PostedOperatorDep, local

PENDING = "jatszoter_login"
PENDING_SECONDS = 15 * 60

WRONG = "wrong name or password"
THROTTLED = "too many attempts, try again later"
CHANGE_REQUIRED = "password change required"
NOT_OPERATOR = Flash(
    "refusal",
    "Refused",
    "This account is not an operator's and was logged out. "
    "Trainees open the link they were given.",
)
DIFFER = "the two passwords differ"
PASSWORD_LENGTH = 12
WEAK = f"at least {PASSWORD_LENGTH} characters and three of: lowercase, uppercase, digits, symbols"
UNCHANGED = "must differ from the current password"
EXPIRED = "The login form has expired. Nothing was sent; enter your name and password again"

router = APIRouter()

templates.env.globals.update(
    password_length=PASSWORD_LENGTH, password_rule=WEAK, passwords_differ=DIFFER
)


class Pending(BaseModel):
    """A login that waits for its new password, sealed into the form instead of rendered."""

    name: str
    password: str
    issued_at: int


def field(form: FormData, name: str) -> str:
    value = form.get(name)
    return value if isinstance(value, str) else ""


def weak(password: str) -> bool:
    """Whether `password` breaks the API's rule: 12 characters and three of four classes."""
    classes = (str.islower, str.isupper, str.isdigit, lambda char: not char.isalnum())
    mixed = sum(any(map(test, password)) for test in classes)
    return len(password) < PASSWORD_LENGTH or mixed < 3


def password_errors(
    password: str, repeat: str, name: str, current: str | None = None
) -> dict[str, str]:
    """The field errors of a new password and its repeat, found before the API is asked."""
    errors: dict[str, str] = {}
    if weak(password):
        errors[name] = WEAK
    elif password == current:
        errors[name] = UNCHANGED
    if repeat != password:
        errors["repeat_password"] = DIFFER
    return errors


def page(request: Request, context: dict[str, Any], status: int = 200) -> Response:
    return render(request, "login.html", {"csrf_token": browser_token(request), **context}, status)


@router.get("/")
def root(_: OperatorDep) -> RedirectResponse:
    return RedirectResponse(LANDING, status_code=303)


@router.get("/login")
def login_page(request: Request, next: str | None = None) -> Response:
    return page(request, {"next": local(next)})


@router.post("/login")
async def login(request: Request) -> Response:
    try:
        await check_browser(request)
    except HTTPException as refused:
        return await expired(request, refused.status_code)
    form = await request.form()
    api: Api = request.app.state.api
    keys: dict[str, bytes] = request.app.state.keys
    next_page = local(field(form, "next"))
    context: dict[str, Any] = {"next": next_page}
    if "pending" in form:
        pending = unsealed_pending(field(form, "pending"), keys)
        if pending is None:
            return page(request, context)
        context.update(name=pending.name, pending=field(form, "pending"))
        new_password = field(form, "new_password")
        repeat = field(form, "repeat_password")
        errors = password_errors(new_password, repeat, "new_password", pending.password)
        if errors:
            return page(request, {**context, "errors": errors}, 422)
        payload = {"name": pending.name, "password": pending.password}
        payload["new_password"] = new_password
    else:
        payload = {"name": field(form, "name"), "password": field(form, "password")}
        context["name"] = payload["name"]
    try:
        response = await api.anonymous("POST", "/api/v1/auth/login", payload)
        if response.status_code == 200:
            return await logged_in(request, response, next_page, context)
    except ApiDown as down:
        return page(request, {**context, "flashes": [error(down.message)]}, down.status)
    status = response.status_code
    if status == 401:
        return page(request, {**context, "flashes": [refusal(WRONG, status)]}, status)
    if status == 429:
        return page(request, {**context, "flashes": [refusal(THROTTLED, status)]}, status)
    if status == 403 and message(response) == CHANGE_REQUIRED:
        sealed = seal_pending(payload["name"], payload["password"], request)
        return page(request, {**context, "pending": sealed})
    if status >= 500:
        failure = failed(status)
        return page(request, {**context, "flashes": [error(failure.message)]}, failure.status)
    return page(request, {**context, "flashes": [refusal(message(response), status)]}, status)


async def expired(request: Request, status: int) -> Response:
    """The login form again, with the name kept and a fresh token, after a refused CSRF check."""
    form = await request.form()
    context = {"next": local(field(form, "next")), "name": field(form, "name")}
    return page(request, {**context, "flashes": [refusal(EXPIRED, status)]}, status)


def seal_pending(name: str, password: str, request: Request) -> str:
    pending = Pending(name=name, password=password, issued_at=int(time.time()))
    kid: str = request.app.state.settings.cookie_kid
    return seal(pending.model_dump_json().encode(), request.app.state.keys, kid, PENDING)


def unsealed_pending(value: str, keys: dict[str, bytes]) -> Pending | None:
    plain = unseal(value, keys, PENDING)
    if plain is None:
        return None
    try:
        pending = Pending.model_validate_json(plain)
    except ValidationError:
        return None
    return pending if time.time() - pending.issued_at <= PENDING_SECONDS else None


async def logged_in(
    request: Request, response: httpx.Response, next_page: str | None, context: dict[str, Any]
) -> Response:
    """The session cookie for an operator; anyone else is logged out at once."""
    api: Api = request.app.state.api
    try:
        answer = response.json()
        issued = answer["credential"]
        operator = Operator(
            user_id=uuid.UUID(answer["user_id"]),
            name="",
            credential=issued["credential"],
            expires_at=int(issued["expires_at"]),
            key=issued["key"],
            csrf=new_token(),
        )
    except (KeyError, TypeError, ValueError):
        raise failed(200) from None
    me = await api.signed(operator, "GET", "/api/v1/auth/me")
    try:
        user = me.json() if me.status_code == 200 else None
    except ValueError:
        user = None
    if not isinstance(user, dict):
        raise failed(me.status_code)
    if user.get("role") != "operator":
        with suppress(ApiDown):
            await api.signed(operator, "POST", "/api/v1/auth/logout")
        return page(request, {**context, "flashes": [NOT_OPERATOR]}, 403)
    operator = operator.model_copy(update={"name": str(user.get("name", ""))})
    kid: str = request.app.state.settings.cookie_kid
    queue_cookie(request, SESSION_COOKIE, seal_operator(operator, request.app.state.keys, kid))
    return RedirectResponse(next_page or LANDING, status_code=303)


@router.post("/logout")
async def logout(request: Request, session: PostedOperatorDep) -> Response:
    """End the session whatever the API answers."""
    if session.operator.expires_at > time.time():
        with suppress(ApiDown):
            await session.api.signed(session.operator, "POST", "/api/v1/auth/logout")
    queue_cookie(request, SESSION_COOKIE, None)
    return RedirectResponse("/login", status_code=303)
