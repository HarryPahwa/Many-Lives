"""The file-backed engine: the §29.2 demo beat without Atlas.

`FileBackedEngine` exists so "kill the process, resume, state is correct" can
be demonstrated before A's persistence lands, and still works if Atlas is
unavailable on the day. It is insurance, not a destination.

This module re-runs the §20.2 restart assertions with `STUB_STATE_FILE` set,
so the paths that skip in `test_persistence.py` are actually exercised
somewhere in the suite rather than merely queued.
"""

from __future__ import annotations

import importlib
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    """Point the seam at a throwaway state file and rebuild the engine."""
    path = tmp_path / "world.json"
    monkeypatch.setenv("STUB_STATE_FILE", str(path))
    get_settings.cache_clear()
    stubs = importlib.import_module("app.services.stubs")
    stubs.reset_stubs()
    yield path
    stubs.reset_stubs()
    monkeypatch.delenv("STUB_STATE_FILE", raising=False)
    stubs.reset_stubs()


def _client() -> TestClient:
    return TestClient(importlib.import_module("app.main").app)


def _restart() -> TestClient:
    """A genuinely new process image: every module rebuilt from scratch."""
    for name in (
        "app.services.stubs",
        "app.services.turn_orchestrator",
        "app.api.routes_campaigns",
        "app.api.routes_turns",
        "app.api.routes_debug",
        "app.main",
    ):
        importlib.reload(importlib.import_module(name))
    return TestClient(importlib.import_module("app.main").app)


def _create(client: TestClient, seed: int = 9) -> str:
    return client.post(
        "/api/campaigns", json={"player_name": "Ada", "seed": seed}
    ).json()["campaign"]["campaign_id"]


def _turn(client: TestClient, campaign_id: str, text: str) -> dict:
    return client.post(
        f"/api/campaigns/{campaign_id}/turns",
        json={"turn_id": str(uuid.uuid4()), "player_id": "player_1", "input": text},
    ).json()


def _resume(client: TestClient, campaign_id: str) -> dict:
    return client.post(
        f"/api/campaigns/{campaign_id}/resume", json={"player_id": "player_1"}
    ).json()


def test_file_backed_engine_declares_itself_durable(state_file):
    stubs = importlib.import_module("app.services.stubs")
    engine = stubs.get_engine()
    assert engine.DURABLE is True
    assert isinstance(engine, stubs.FileBackedEngine)


def test_the_demo_beat_kill_restart_resume(state_file):
    """§29.2 steps 2–4: the whole point of the project, end to end."""
    client = _client()
    campaign_id = _create(client)

    room = _turn(client, campaign_id, "north")["visible_cell"]
    item = room["items"][0]
    _turn(client, campaign_id, f"take {item['name']}")

    before_sheet = client.get(f"/api/campaigns/{campaign_id}/player").json()
    before_turn = client.get(f"/api/campaigns/{campaign_id}").json()["current_turn"]

    client = _restart()

    resumed = _resume(client, campaign_id)
    assert "error" not in resumed, resumed
    assert resumed["player"]["cell_id"] == before_sheet["cell_id"]
    assert resumed["visible_cell"]["name"] == room["name"]
    assert resumed["campaign"]["current_turn"] == before_turn

    after_sheet = client.get(f"/api/campaigns/{campaign_id}/player").json()
    assert after_sheet["keys_held"] == before_sheet["keys_held"] == 1
    assert [i["id"] for i in after_sheet["carried"]] == [
        i["id"] for i in before_sheet["carried"]
    ]


def test_a_taken_item_does_not_respawn_after_a_restart(state_file):
    """§20.2 #4, actually exercised."""
    client = _client()
    campaign_id = _create(client)
    item = _turn(client, campaign_id, "north")["visible_cell"]["items"][0]
    _turn(client, campaign_id, f"take {item['name']}")

    client = _restart()

    resumed = _resume(client, campaign_id)
    assert item["id"] not in {i["id"] for i in resumed["visible_cell"]["items"]}
    sheet = client.get(f"/api/campaigns/{campaign_id}/player").json()
    assert item["id"] in {i["id"] for i in sheet["carried"]}


def test_room_identity_survives_a_restart(state_file):
    """§20.2 #1 — a generated room is never regenerated, even by a restart."""
    client = _client()
    campaign_id = _create(client)
    first = _turn(client, campaign_id, "north")["visible_cell"]

    client = _restart()

    resumed = _resume(client, campaign_id)
    assert resumed["visible_cell"]["cell_id"] == first["cell_id"]
    assert resumed["visible_cell"]["name"] == first["name"]
    assert resumed["visible_cell"]["description"] == first["description"]


def test_duplicate_turn_id_applies_once_across_a_restart(state_file):
    """§20.2 #6 — the client retries after the server came back."""
    client = _client()
    campaign_id = _create(client)
    turn_id = str(uuid.uuid4())
    body = {"turn_id": turn_id, "player_id": "player_1", "input": "north"}
    first = client.post(f"/api/campaigns/{campaign_id}/turns", json=body).json()

    client = _restart()

    replay = client.post(f"/api/campaigns/{campaign_id}/turns", json=body).json()
    assert replay["turn_sequence"] == first["turn_sequence"]
    assert replay["player"]["cell_id"] == first["player"]["cell_id"]
    assert (
        client.get(f"/api/campaigns/{campaign_id}").json()["current_turn"]
        == first["turn_sequence"]
    ), "a retry after a restart must not apply the turn a second time"


def test_fog_of_war_survives_a_restart(state_file):
    client = _client()
    campaign_id = _create(client)
    _turn(client, campaign_id, "north")
    before = client.get(f"/api/campaigns/{campaign_id}/map").json()

    client = _restart()

    after = client.get(f"/api/campaigns/{campaign_id}/map").json()
    assert {c["cell_id"] for c in after["cells"]} == {
        c["cell_id"] for c in before["cells"]
    }
    assert after["player_cell"] == before["player_cell"]


def test_campaign_list_survives_a_restart(state_file):
    client = _client()
    first = _create(client, seed=9)
    second = _create(client, seed=42)

    client = _restart()

    listed = {c["campaign_id"] for c in client.get("/api/campaigns").json()}
    assert {first, second} <= listed


def test_the_state_file_is_valid_json_and_holds_no_secrets(state_file):
    client = _client()
    campaign_id = _create(client)
    _turn(client, campaign_id, "north")

    payload = json.loads(state_file.read_text(encoding="utf-8"))
    assert payload["campaigns"], "the world should have been written"
    text = state_file.read_text(encoding="utf-8")
    for forbidden in ("mongodb+srv", "sk-or-", "OPENROUTER"):
        assert forbidden not in text, "state file must never capture credentials"


def test_a_corrupt_state_file_does_not_take_the_server_down(state_file):
    """Mid-demo robustness: start empty rather than crash on load."""
    client = _client()
    _create(client)
    state_file.write_text("{not json at all", encoding="utf-8")

    client = _restart()

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/campaigns").json() == []
    # The damaged file is kept for inspection rather than silently discarded.
    assert state_file.with_suffix(".corrupt").exists()


def test_writes_are_atomic_leaving_no_temp_file_behind(state_file):
    client = _client()
    campaign_id = _create(client)
    _turn(client, campaign_id, "north")
    assert not state_file.with_suffix(".tmp").exists()
