"""Scaffold smoke test: the app package imports and the API boots.

Replaced/expanded by the real unit suite (TDD §20.1) as modules land.
"""

from fastapi.testclient import TestClient

from app.main import app


def test_health():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
