import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from jatszoter_web.api import ApiDown
from jatszoter_web.pages import Flash, error, fragment, redirect, render
from jatszoter_web.screens import ROLES, came_from, field, read, refused, swapped
from jatszoter_web.session import OperatorDep, PostedOperatorDep, Session, local

router = APIRouter()

TABLE = "users-table"
DIFFER = "the two passwords differ"
# A name that is taken is the one conflict a new user meets.
NEW_CONCERNS = {"": "name"}
PASSWORD_CONCERNS = {"a password": "password"}


@router.get("/users")
async def users(request: Request, session: OperatorDep, role: str = "") -> Response:
    role = role if role in ROLES else ""
    context: dict[str, Any] = {"current": "/users", "role": role, "roles": ROLES}
    try:
        context["rows"] = await read(session, "/api/v1/users", {"role": role} if role else None)
        code = 200
    except ApiDown as down:
        context.update(rows=[], failure=True, flashes=[error(down.message)])
        code = down.status
    if swapped(request, TABLE):
        return fragment(request, "users_table.html", context, code)
    return render(request, "users.html", context, code)


def dialog(request: Request, name: str, context: dict[str, Any], status: int = 200) -> Response:
    return fragment(request, name, {"values": {}, "errors": {}, **context}, status)


@router.get("/users/new")
async def user_dialog(request: Request, _: OperatorDep) -> Response:
    return dialog(request, "user_new.html", {"roles": ROLES})


@router.post("/users")
async def create_user(request: Request, session: PostedOperatorDep) -> Response:
    form = await request.form()
    values = {name: field(form, name) for name in ("name", "role", "comment")}
    payload = {**values, "comment": values["comment"] or None}
    context: dict[str, Any] = {"roles": ROLES, "values": values}
    try:
        response = await session.call("POST", "/api/v1/users", payload)
    except ApiDown as down:
        return dialog(
            request, "user_new.html", {**context, "flashes": [error(down.message)]}, down.status
        )
    if response.status_code != 201:
        concerns = NEW_CONCERNS if response.status_code == 409 else None
        flash, errors = refused(response, concerns)
        context.update(errors=errors, flashes=[flash])
        return dialog(request, "user_new.html", context, response.status_code)
    name = response.json()["name"]
    text = f"{name} is enabled; set a one-time password so they can log in."
    return redirect(request, "/users", Flash("success", "User created", text))


async def named(session: Session, user_id: uuid.UUID) -> str:
    for row in await read(session, "/api/v1/users"):
        if row["id"] == str(user_id):
            return str(row["name"])
    return "the user"


@router.get("/users/{user_id:uuid}/password")
async def password_dialog(request: Request, session: OperatorDep, user_id: uuid.UUID) -> Response:
    context = {"id": user_id, "name": await named(session, user_id)}
    return dialog(request, "user_password.html", context)


@router.post("/users/{user_id:uuid}/password")
async def set_password(
    request: Request, session: PostedOperatorDep, user_id: uuid.UUID
) -> Response:
    form = await request.form()
    password = form.get("password")
    password = password if isinstance(password, str) else ""
    name = await named(session, user_id)
    context: dict[str, Any] = {"id": user_id, "name": name}
    if password != form.get("repeat_password"):
        return dialog(
            request, "user_password.html", {**context, "errors": {"repeat_password": DIFFER}}, 422
        )
    try:
        response = await session.call(
            "POST", f"/api/v1/users/{user_id}/password", {"password": password}
        )
    except ApiDown as down:
        return dialog(
            request,
            "user_password.html",
            {**context, "flashes": [error(down.message)]},
            down.status,
        )
    if response.status_code != 204:
        flash, errors = refused(response, PASSWORD_CONCERNS)
        context.update(errors=errors, flashes=[flash])
        return dialog(request, "user_password.html", context, response.status_code)
    text = f"{name} must change it at the next login."
    return redirect(request, "/users", Flash("success", "Password set", text))


@router.get("/users/{user_id:uuid}/state")
async def user_state_dialog(request: Request, session: OperatorDep, user_id: uuid.UUID) -> Response:
    context = {
        "id": user_id,
        "name": await named(session, user_id),
        "next": came_from(request, "/users"),
    }
    return fragment(request, "user_disable.html", context)


@router.post("/users/{user_id:uuid}/state")
async def switch_user(request: Request, session: PostedOperatorDep, user_id: uuid.UUID) -> Response:
    form = await request.form()
    back = local(field(form, "next")) or "/users"
    state = field(form, "state")
    response = await session.call("POST", f"/api/v1/users/{user_id}/state", {"state": state})
    if response.status_code != 200:
        return redirect(request, back, refused(response)[0])
    name = response.json()["name"]
    if state == "disabled":
        flash = Flash("success", "User disabled", f"{name} can no longer log in.")
    else:
        flash = Flash("success", "User enabled", f"{name} can log in again.")
    return redirect(request, back, flash)
