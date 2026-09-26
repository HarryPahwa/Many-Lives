from fastapi.testclient import TestClient

from app.main import app


def test_evaluation_routes_refuse_the_demo_stub():
    response = TestClient(app).post("/api/evaluations/run", json={})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
