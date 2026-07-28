from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_health_endpoint_returns_public_process_status() -> None:
    client = TestClient(create_app(settings=Settings(_env_file=None)))

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "BizOrch",
        "version": "0.1.0",
        "environment": "development",
    }


def test_openapi_uses_bizorch_identity() -> None:
    client = TestClient(create_app(settings=Settings(_env_file=None)))

    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "BizOrch"
