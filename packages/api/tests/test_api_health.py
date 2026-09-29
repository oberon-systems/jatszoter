from fastapi.testclient import TestClient

from jatszoter_api.app import create_app


def test_healthz_is_public() -> None:
    response = TestClient(create_app()).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_schema_is_not_exposed() -> None:
    client = TestClient(create_app())

    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404
