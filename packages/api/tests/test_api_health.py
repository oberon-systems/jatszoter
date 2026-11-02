import pytest
from fastapi.testclient import TestClient

from jatszoter_api.app import create_app
from jatszoter_api.settings import Settings


def test_healthz_is_public(settings: Settings) -> None:
    response = TestClient(create_app(settings)).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_schema_is_not_exposed(settings: Settings) -> None:
    client = TestClient(create_app(settings))

    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/api/v1/scenarios/", "/api/v1/scenarios%2f", "/healthz/"])
@pytest.mark.parametrize("host", ["alpha.example.com", "alpha.example.com@beta.example.com"])
def test_a_trailing_slash_does_not_redirect_to_the_host_header(
    settings: Settings, path: str, host: str
) -> None:
    client = TestClient(create_app(settings), follow_redirects=False)

    response = client.get(path, headers={"host": host})

    assert response.status_code == 404
    assert "location" not in response.headers


def test_the_app_keeps_the_settings_it_was_given(settings: Settings) -> None:
    assert create_app(settings).state.settings is settings
