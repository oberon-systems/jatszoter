from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from jatszoter_web.api import ApiDown
from jatszoter_web.pages import error, fragment, render
from jatszoter_web.screens import by_id, read, swapped
from jatszoter_web.session import OperatorDep

router = APIRouter()

TABLE = "results-table"


def rows(checks: list[dict[str, Any]], deployments: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per checked deployment, newest check first, as the API lists the checks."""
    found: dict[str, dict[str, Any]] = {}
    for check in checks:
        row = found.get(check["deployment_id"])
        if row is None:
            deployment = deployments.get(check["deployment_id"])
            if deployment is None:
                continue
            found[check["deployment_id"]] = {"deployment": deployment, "last": check, "checks": 1}
        else:
            row["checks"] += 1
    return list(found.values())


def kept(row: dict[str, Any], scenarios: dict[str, Any], scenario: str, trainee: str) -> bool:
    deployment = row["deployment"]
    slug = scenarios.get(deployment["scenario_id"], {}).get("slug")
    return (not scenario or slug == scenario) and (
        not trainee or deployment["trainee_id"] == trainee
    )


@router.get("/results")
async def results(
    request: Request, session: OperatorDep, scenario: str = "", trainee: str = ""
) -> Response:
    context: dict[str, Any] = {"current": "/results", "scenario": scenario, "trainee": trainee}
    try:
        scenarios = by_id(await read(session, "/api/v1/scenarios"))
        users = by_id(await read(session, "/api/v1/users"))
        deployments = by_id(await read(session, "/api/v1/deployments"))
        found = rows(await read(session, "/api/v1/checks"), deployments)
        context.update(
            scenarios=scenarios,
            users=users,
            slugs=sorted({row["slug"] for row in scenarios.values()}),
            trainees=sorted(
                (row for row in users.values() if row["role"] == "trainee"),
                key=lambda row: row["name"],
            ),
        )
        context["rows"] = [row for row in found if kept(row, scenarios, scenario, trainee)]
        code = 200
    except ApiDown as down:
        context.update(rows=[], failure=True, flashes=[error(down.message)])
        code = down.status
    if swapped(request, TABLE):
        return fragment(request, "results_table.html", context, code)
    return render(request, "results.html", context, code)
