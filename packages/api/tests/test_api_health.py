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


def test_the_app_keeps_the_settings_it_was_given(settings: Settings) -> None:
    assert create_app(settings).state.settings is settings
