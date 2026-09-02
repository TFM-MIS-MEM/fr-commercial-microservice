from fastapi.testclient import TestClient

from src.core.settings import settings
from src.main import app

client = TestClient(app)


def test_health_check():
    response = client.get(f"{settings.api_v1_str}/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == settings.project_name
    assert "version" in data
    assert "environment" in data
