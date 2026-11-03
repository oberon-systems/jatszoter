import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from jatszoter_api.answers import field_code
from jatszoter_api.app import create_app
from jatszoter_api.settings import Settings

REGISTER = "/api/v1/agents/register"


@pytest.mark.parametrize(
    ("errors", "code"),
    [
        ([{"loc": ("body", 1), "type": "json_invalid"}], "body-invalid"),
        ([{"loc": ("body",), "type": "missing"}], "body-missing"),
        ([{"loc": ("body", "deployment_id"), "type": "missing"}], "deployment-id-missing"),
        ([{"loc": ("body", "labels", 0), "type": "string_too_long"}], "labels-invalid"),
        ([{"loc": ("query", "role"), "type": "enum"}], "role-invalid"),
        ([{"loc": ("url",), "type": "url_parsing"}], "url-invalid"),
        ([{"loc": ("body", "Alpha Beta"), "type": "extra_forbidden"}], "invalid-request"),
        ([{"loc": ("body", 0), "type": "dict_type"}], "invalid-request"),
        ([], "invalid-request"),
    ],
)
def test_a_validation_error_is_named_by_its_top_level_field(
    errors: list[dict[str, Any]], code: str
) -> None:
    assert field_code(errors) == code


def test_a_body_that_is_not_json_answers_a_code_without_the_parser_text(
    api: TestClient, refused: Any
) -> None:
    response = api.post(
        f"/api/v1/agents/{uuid.uuid4()}/heartbeat",
        content=b"{alpha",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    refused(response, "body-invalid", "json_invalid")


def test_a_missing_field_is_named_and_nothing_sent_comes_back(
    api: TestClient, refused: Any
) -> None:
    response = api.post(REGISTER, json={"labels": ["beta"]})

    assert response.status_code == 422
    refused(response, "name-missing")


def test_a_long_value_is_not_repeated_in_the_answer(api: TestClient, refused: Any) -> None:
    response = api.post(REGISTER, json={"name": "alpha", "labels": ["b" * 50_000]})

    assert response.status_code == 422
    assert len(response.content) < 200
    refused(response, "labels-invalid")


def test_an_unknown_route_answers_a_code(api: TestClient, refused: Any) -> None:
    response = api.get("/api/v1/alpha")

    assert response.status_code == 404
    refused(response, "not-found")


def test_a_link_token_never_reaches_the_log(
    api: TestClient, refused: Any, caplog: pytest.LogCaptureFixture
) -> None:
    response = api.get("/s/beta-token")

    assert response.status_code == 404
    error_id = refused(response, "not-found")
    assert error_id in caplog.text
    assert "beta-token" not in caplog.text


def test_an_unexpected_exception_answers_internal_error_and_logs_the_traceback(
    session: Session, settings: Settings, refused: Any, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(settings)

    def broken() -> None:
        raise RuntimeError("alpha secret")

    app.add_api_route("/broken", broken)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/broken")

    assert response.status_code == 500
    assert "alpha secret" not in response.text
    refused(response, "internal-error")
    assert "alpha secret" in caplog.text
