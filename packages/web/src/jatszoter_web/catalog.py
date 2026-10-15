from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response
from starlette.datastructures import UploadFile

from jatszoter_web.api import ApiDown
from jatszoter_web.pages import Flash, error, fragment, redirect, refusal, render
from jatszoter_web.screens import field, read, refused
from jatszoter_web.session import OperatorDep, PostedOperatorDep

router = APIRouter()

BUNDLE_LIMIT = 10 * 1024 * 1024
TOO_LARGE = f"the bundle is larger than {BUNDLE_LIMIT} bytes"
ORDER = {
    "scenarios": lambda row: (row["slug"], row["version"]),
    "images": lambda row: row["name"],
}


async def tab(request: Request, session: OperatorDep, which: str) -> Response:
    context: dict[str, Any] = {"current": "/scenarios", "tab": which}
    try:
        rows = await read(session, f"/api/v1/{which}")
        context["rows"] = sorted(rows, key=ORDER[which])
        code = 200
    except ApiDown as down:
        context.update(rows=[], failure=True, flashes=[error(down.message)])
        code = down.status
    return render(request, "catalog.html", context, code)


@router.get("/scenarios")
async def scenarios(request: Request, session: OperatorDep) -> Response:
    return await tab(request, session, "scenarios")


@router.get("/images")
async def images(request: Request, session: OperatorDep) -> Response:
    return await tab(request, session, "images")


def dialog(request: Request, name: str, context: dict[str, Any], status: int = 200) -> Response:
    return fragment(request, name, {"values": {}, "errors": {}, **context}, status)


@router.get("/scenarios/new")
async def scenario_dialog(request: Request, _: OperatorDep) -> Response:
    return dialog(request, "scenario_new.html", {})


@router.post("/scenarios")
async def register_scenario(request: Request, session: PostedOperatorDep) -> Response:
    form = await request.form(max_files=1, max_fields=4)
    url = field(form, "url")
    bundle = form.get("bundle")
    values = {"url": url}
    files = None
    if isinstance(bundle, UploadFile) and bundle.filename:
        data = await bundle.read(BUNDLE_LIMIT + 1)
        if len(data) > BUNDLE_LIMIT:
            flash = refusal(TOO_LARGE, 413)
            context = {"values": values, "errors": {"bundle": TOO_LARGE}, "flashes": [flash]}
            return dialog(request, "scenario_new.html", context, 413)
        files = {"bundle": (bundle.filename, data)}
    try:
        if files is not None:
            response = await session.call("POST", "/api/v1/scenarios", files=files)
        else:
            response = await session.call("POST", "/api/v1/scenarios", {"url": url})
    except ApiDown as down:
        context = {"values": values, "flashes": [error(down.message)]}
        return dialog(request, "scenario_new.html", context, down.status)
    if response.status_code != 201:
        flash, errors = refused(response)
        context = {"values": values, "errors": errors, "flashes": [flash]}
        return dialog(request, "scenario_new.html", context, response.status_code)
    row = response.json()
    text = f"{row['slug']} v{row['version']} can be deployed now."
    return redirect(request, "/scenarios", Flash("success", "Scenario registered", text))


@router.get("/images/new")
async def image_dialog(request: Request, _: OperatorDep) -> Response:
    return dialog(request, "image_new.html", {})


@router.post("/images")
async def register_image(request: Request, session: PostedOperatorDep) -> Response:
    form = await request.form()
    values = {name: field(form, name) for name in ("name", "url", "digest")}
    try:
        response = await session.call("POST", "/api/v1/images", values)
    except ApiDown as down:
        context = {"values": values, "flashes": [error(down.message)]}
        return dialog(request, "image_new.html", context, down.status)
    if response.status_code != 201:
        flash, errors = refused(response)
        context = {"values": values, "errors": errors, "flashes": [flash]}
        return dialog(request, "image_new.html", context, response.status_code)
    text = f"{response.json()['name']} can be named by scenarios now."
    return redirect(request, "/images", Flash("success", "Image registered", text))
