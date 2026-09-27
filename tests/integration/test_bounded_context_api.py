"""The two routes behind the bounded-context claim (§11 budget, §16.4 result).

The UI states the claim, so these tests guard the numbers it is allowed to
state: stored history is campaign-scoped and grows with play, the per-call
context stays inside the policy budget while it does, and the measured P07 run
is served from the artefact rather than from anything the page invents.
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.routes_evals import P07_RESULT_FILE
from app.config import get_settings
from app.services.stubs import reset_stubs


def _app():
    """The current FastAPI app; see the note in test_turn_loop.py."""
    import importlib

    return importlib.import_module("app.main").app


@pytest.fixture(autouse=True)
def _fresh_world(monkeypatch):
    monkeypatch.setenv("DEBUG_ENDPOINTS", "true")
    get_settings.cache_clear()
    reset_stubs()
    yield
    get_settings.cache_clear()
    reset_stubs()


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app())


def _create(client: TestClient, name: str = "Ada", seed: int = 42) -> str:
    response = client.post("/api/campaigns", json={"player_name": name, "seed": seed})
    assert response.status_code == 201, response.text
    return response.json()["campaign"]["campaign_id"]


def _turn(client: TestClient, campaign_id: str, text: str) -> dict:
    return client.post(
        f"/api/campaigns/{campaign_id}/turns",
        json={"turn_id": str(uuid.uuid4()), "player_id": "player_1", "input": text},
    ).json()


# ---------------------------------------------------------------------------
# Stored history (§16.4)
# ---------------------------------------------------------------------------


def test_history_grows_with_play_while_context_stays_in_budget(client: TestClient):
    campaign_id = _create(client)
    before = client.get(f"/api/campaigns/{campaign_id}/history").json()

    for _ in range(6):
        _turn(client, campaign_id, "look")
    after = client.get(f"/api/campaigns/{campaign_id}/history").json()

    assert after["supported"] is True
    assert after["turns"] > before["turns"]
    assert after["stored_bytes"] >= before["stored_bytes"]
    assert after["budget_tokens"] > 0

    # The claim under test: history moved, the per-call context did not leave
    # its budget. A free-form turn is the one that actually builds context.
    result = _turn(client, campaign_id, "study the carvings on the far wall")
    debug = client.get(
        f"/api/campaigns/{campaign_id}/debug/context",
        params={"turn_id": result["turn_id"]},
    ).json()
    manifest = debug["context_manifest"]
    assert manifest is not None, "a free-form turn must build context"
    assert manifest["budget_tokens"] == after["budget_tokens"]
    assert 0 < manifest["estimated_tokens"] <= manifest["budget_tokens"]


def test_history_is_scoped_to_one_campaign(client: TestClient):
    """§5.10 / INV-13 — one campaign's history never counts another's."""
    busy = _create(client, "Ada", seed=42)
    quiet = _create(client, "Bo", seed=99)
    for _ in range(4):
        _turn(client, busy, "look")

    busy_stats = client.get(f"/api/campaigns/{busy}/history").json()
    quiet_stats = client.get(f"/api/campaigns/{quiet}/history").json()

    assert busy_stats["campaign_id"] == busy
    assert busy_stats["turns"] > quiet_stats["turns"]


def test_history_for_an_unknown_campaign_is_404(client: TestClient):
    response = client.get("/api/campaigns/cmp_does_not_exist/history")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# The measured run (P07)
# ---------------------------------------------------------------------------


def test_bounded_context_route_serves_the_stored_measurement(client: TestClient):
    if not P07_RESULT_FILE.exists():
        pytest.skip("docs/p07_result.json absent; run scripts/seed_stress_history.py")
    body = client.get("/api/evaluations/bounded-context").json()

    assert body == json.loads(P07_RESULT_FILE.read_text(encoding="utf-8"))
    # The fields the UI renders; a missing one would silently blank the panel.
    for field in (
        "budget_tokens",
        "baseline_events",
        "baseline_tokens",
        "peak_estimated_tokens",
        "history_growth_factor",
        "token_growth_factor",
    ):
        assert field in body, f"{field} is missing from the stored result"
    assert body["checkpoints"], "the result must carry at least one checkpoint"
    for point in body["checkpoints"]:
        assert {"events", "estimated_tokens", "stored_bytes"} <= set(point)
        assert point["estimated_tokens"] <= body["budget_tokens"]


def test_bounded_context_route_404s_without_a_measurement(
    client: TestClient, monkeypatch
):
    """An unmeasured claim is absent, never fabricated."""
    monkeypatch.setattr(
        "app.api.routes_evals.P07_RESULT_FILE", P07_RESULT_FILE.with_name("missing.json")
    )
    assert client.get("/api/evaluations/bounded-context").status_code == 404
