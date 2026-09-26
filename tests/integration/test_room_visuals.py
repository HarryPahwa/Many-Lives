"""Room Visuals §14.2 — API and invariants, with the fake image client.

The invariants matter more than the happy path here. This is an optional
feature bolted onto a working game, so the tests that earn their keep are the
ones proving it cannot hurt anything: flag off changes nothing, turns never
touch the image client, engine state is untouched by rendering, and a failing
provider is invisible to play.
"""

from __future__ import annotations

import concurrent.futures
import importlib
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services import image_client as image_client_module
from app.services import visual_store as visual_store_module
from app.services.image_client import FakeImageClient, ImageGenerationError
from app.services.stubs import get_engine, reset_stubs
from app.services.visual_store import MemoryVisualStore


def _app():
    return importlib.import_module("app.main").app


@pytest.fixture
def fake_images():
    client = FakeImageClient()
    image_client_module.set_client(client)
    yield client
    image_client_module.set_client(None)


@pytest.fixture
def visuals_on(monkeypatch, fake_images):
    """Feature enabled, fake image client, in-memory store."""
    monkeypatch.setenv("ENABLE_ROOM_VISUALS", "true")
    monkeypatch.setenv("IMAGE_CLIENT", "fake")
    monkeypatch.setenv("USE_FAKE_MODELS", "true")
    get_settings.cache_clear()
    reset_stubs()
    visual_store_module.set_store(MemoryVisualStore())
    yield fake_images
    visual_store_module.set_store(None)
    get_settings.cache_clear()
    reset_stubs()


@pytest.fixture
def visuals_off(monkeypatch):
    monkeypatch.setenv("ENABLE_ROOM_VISUALS", "false")
    get_settings.cache_clear()
    reset_stubs()
    visual_store_module.set_store(MemoryVisualStore())
    yield
    visual_store_module.set_store(None)
    get_settings.cache_clear()
    reset_stubs()


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app())


def _create(client: TestClient, seed: int = 9) -> tuple[str, str]:
    body = client.post("/api/campaigns", json={"player_name": "Ada", "seed": seed}).json()
    return body["campaign"]["campaign_id"], body["initial"]["visible_cell"]["cell_id"]


def _turn(client: TestClient, campaign_id: str, text: str) -> dict:
    return client.post(
        f"/api/campaigns/{campaign_id}/turns",
        json={"turn_id": str(uuid.uuid4()), "player_id": "player_1", "input": text},
    ).json()


def _visual(client: TestClient, campaign_id: str, cell_id: str):
    return client.get(f"/api/campaigns/{campaign_id}/cells/{cell_id}/visual")


def _request(client: TestClient, campaign_id: str, cell_id: str):
    return client.post(f"/api/campaigns/{campaign_id}/cells/{cell_id}/visual")


# ---------------------------------------------------------------------------
# VIS-03 — flag off changes nothing
# ---------------------------------------------------------------------------


def test_every_visual_route_is_404_when_the_flag_is_off(visuals_off, client):
    campaign_id, cell_id = _create(client)
    assert _visual(client, campaign_id, cell_id).status_code == 404
    assert _request(client, campaign_id, cell_id).status_code == 404
    assert client.get(
        f"/api/campaigns/{campaign_id}/visual-assets/va_{'0' * 32}"
    ).status_code == 404


def test_turns_are_unaffected_when_the_flag_is_off(visuals_off, client):
    campaign_id, _ = _create(client)
    result = _turn(client, campaign_id, "look")
    assert result["accepted"] is True
    assert result["narration"]


def test_the_404_uses_the_standard_error_envelope(visuals_off, client):
    campaign_id, cell_id = _create(client)
    body = _visual(client, campaign_id, cell_id).json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_a_fresh_cell_reports_NONE_then_renders(visuals_on, client):
    fake = visuals_on
    campaign_id, cell_id = _create(client)

    first = _visual(client, campaign_id, cell_id).json()
    assert first["status"] == "NONE"
    assert first["image_url"] is None
    assert first["dirty"] is False

    accepted = _request(client, campaign_id, cell_id)
    assert accepted.status_code == 202

    ready = _visual(client, campaign_id, cell_id).json()
    assert ready["status"] == "READY"
    assert ready["revision"] == 1
    assert ready["image_url"]
    assert fake.call_count == 1

    image = client.get(ready["image_url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"
    assert image.content.startswith(b"\xff\xd8\xff")
    assert "immutable" in image.headers.get("cache-control", "")


def test_an_unchanged_room_is_never_re_rendered(visuals_on, client):
    """VIS-04 — the reuse path is what makes this affordable."""
    fake = visuals_on
    campaign_id, cell_id = _create(client)
    _request(client, campaign_id, cell_id)
    assert fake.call_count == 1

    again = _request(client, campaign_id, cell_id)
    assert again.status_code == 200
    assert fake.call_count == 1, "an unchanged room must not be billed twice"
    assert again.json()["status"] == "READY"


def test_the_same_asset_is_served_after_leaving_and_returning(visuals_on, client):
    fake = visuals_on
    campaign_id, cell_id = _create(client)
    _request(client, campaign_id, cell_id)
    original = _visual(client, campaign_id, cell_id).json()["image_url"]

    moved = _turn(client, campaign_id, "north")
    _turn(client, campaign_id, "south")

    assert _visual(client, campaign_id, cell_id).json()["image_url"] == original
    assert fake.call_count == 1


# ---------------------------------------------------------------------------
# VIS-05 — one render at a time
# ---------------------------------------------------------------------------


def test_concurrent_requests_render_once(visuals_on, client):
    slow = FakeImageClient(delay_s=0.4)
    image_client_module.set_client(slow)
    campaign_id, cell_id = _create(client)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        responses = [
            f.result()
            for f in [pool.submit(_request, client, campaign_id, cell_id) for _ in range(4)]
        ]

    assert all(r.status_code in (200, 202) for r in responses)
    assert slow.call_count == 1, f"{slow.call_count} renders billed for one room"


# ---------------------------------------------------------------------------
# VIS-07 — failure isolation
# ---------------------------------------------------------------------------


def test_a_failing_provider_is_recorded_and_play_continues(visuals_on, client):
    campaign_id, cell_id = _create(client)
    image_client_module.set_client(FakeImageClient(fail_with="HTTP_5XX"))

    _request(client, campaign_id, cell_id)

    status = _visual(client, campaign_id, cell_id).json()
    assert status["status"] == "FAILED"
    assert status["error_code"] == "HTTP_5XX"

    # The game is untouched.
    assert _turn(client, campaign_id, "look")["accepted"] is True


def test_a_failure_keeps_the_previous_picture_on_screen(visuals_on, client):
    campaign_id, cell_id = _create(client)
    _request(client, campaign_id, cell_id)
    good_url = _visual(client, campaign_id, cell_id).json()["image_url"]

    # Change the room so the next request is not short-circuited, then fail.
    _turn(client, campaign_id, "north")
    _turn(client, campaign_id, "south")
    image_client_module.set_client(FakeImageClient(fail_with="TIMEOUT"))
    from app.services import visual_service
    from app.services.visual_store import get_store

    record = get_store().get(campaign_id, cell_id)
    record.signature = "sha256:stale"
    get_store().put(record)
    _request(client, campaign_id, cell_id)

    after = _visual(client, campaign_id, cell_id).json()
    assert after["status"] == "FAILED"
    assert after["image_url"] == good_url, "the old image must still be served"


def test_an_unexpected_crash_is_contained(visuals_on, client):
    class Exploding:
        def generate(self, prompt, *, references):
            raise ValueError("something unexpected")

    campaign_id, cell_id = _create(client)
    image_client_module.set_client(Exploding())

    _request(client, campaign_id, cell_id)

    status = _visual(client, campaign_id, cell_id).json()
    assert status["status"] == "FAILED"
    assert status["error_code"] == "INTERNAL"
    assert _turn(client, campaign_id, "look")["accepted"] is True


# ---------------------------------------------------------------------------
# VIS-06 — scoping
# ---------------------------------------------------------------------------


def test_an_undiscovered_cell_has_no_visual(visuals_on, client):
    campaign_id, _ = _create(client)
    assert _visual(client, campaign_id, "cell_6_6").status_code == 404
    assert _request(client, campaign_id, "cell_6_6").status_code == 404


def test_an_asset_from_another_campaign_is_404(visuals_on, client):
    first, first_cell = _create(client, seed=9)
    _request(client, first, first_cell)
    url = _visual(client, first, first_cell).json()["image_url"]
    asset_id = url.rsplit("/", 1)[-1]

    second, _ = _create(client, seed=42)
    assert client.get(
        f"/api/campaigns/{second}/visual-assets/{asset_id}"
    ).status_code == 404


@pytest.mark.parametrize(
    "asset_id", ["../../etc/passwd", "va_nothex", "va_" + "0" * 31, "asset_1", "va_%2e%2e"]
)
def test_malformed_asset_ids_are_404(visuals_on, client, asset_id):
    campaign_id, _ = _create(client)
    assert client.get(
        f"/api/campaigns/{campaign_id}/visual-assets/{asset_id}"
    ).status_code == 404


def test_an_unknown_campaign_is_404(visuals_on, client):
    assert _visual(client, "cmp_nope", "cell_0_0").status_code == 404
    assert _request(client, "cmp_nope", "cell_0_0").status_code == 404


# ---------------------------------------------------------------------------
# VIS-01 / VIS-02 — the game is untouched
# ---------------------------------------------------------------------------


def test_rendering_does_not_change_engine_state(visuals_on, client):
    """VIS-01 — a picture is a projection, never a cause."""
    campaign_id, cell_id = _create(client)
    engine = get_engine()

    before_view = engine.load_world_view(campaign_id, "player_1")
    before_map = engine.build_map(campaign_id, "player_1").model_dump()
    before_sheet = client.get(f"/api/campaigns/{campaign_id}/player").json()
    before_turn = client.get(f"/api/campaigns/{campaign_id}").json()["current_turn"]

    _request(client, campaign_id, cell_id)
    _visual(client, campaign_id, cell_id)

    after_view = engine.load_world_view(campaign_id, "player_1")
    assert after_view.visible_cell.model_dump() == before_view.visible_cell.model_dump()
    assert after_view.player.model_dump() == before_view.player.model_dump()
    assert engine.build_map(campaign_id, "player_1").model_dump() == before_map
    assert client.get(f"/api/campaigns/{campaign_id}/player").json() == before_sheet
    assert (
        client.get(f"/api/campaigns/{campaign_id}").json()["current_turn"] == before_turn
    )


def test_turns_and_resume_never_call_the_image_client(visuals_on, client):
    """VIS-02 — proven with a client that fails on any call at all."""

    class Forbidden:
        def generate(self, prompt, *, references):
            raise AssertionError("the turn pipeline called the image client")

    campaign_id, _ = _create(client)
    image_client_module.set_client(Forbidden())

    for command in ("look", "north", "wait", "south", "look"):
        assert "error" not in _turn(client, campaign_id, command)

    resumed = client.post(
        f"/api/campaigns/{campaign_id}/resume", json={"player_id": "player_1"}
    )
    assert resumed.status_code == 200


# ---------------------------------------------------------------------------
# Dirty / edit path
# ---------------------------------------------------------------------------


def _walk_to_a_character(client: TestClient, campaign_id: str, steps: int = 16):
    seen: set[str] = set()
    last: str | None = None
    opposite = {"north": "south", "south": "north", "east": "west", "west": "east"}
    offsets = {"north": (0, 1), "south": (0, -1), "east": (1, 0), "west": (-1, 0)}
    for _ in range(steps):
        view = _turn(client, campaign_id, "look")
        cell = view["visible_cell"]
        alive = [c for c in cell["characters"] if c["status"] == "ALIVE"]
        if alive:
            return cell["cell_id"], alive[0]
        seen.add(cell["cell_id"])
        _, sx, sy = cell["cell_id"].split("_")
        x, y = int(sx), int(sy)
        back = opposite.get(last) if last else None
        options = [d for d in cell["exits"] if d != back] or cell["exits"]
        if not options:
            return None, None
        fresh = [
            d for d in options
            if f"cell_{x + offsets[d][0]}_{y + offsets[d][1]}" not in seen
        ]
        last = (fresh or options)[0]
        _turn(client, campaign_id, last)
    return None, None


def test_crossing_a_health_band_marks_the_image_out_of_date(visuals_on, client):
    fake = visuals_on
    campaign_id, _ = _create(client, seed=42)
    cell_id, target = _walk_to_a_character(client, campaign_id)
    if target is None:
        pytest.skip("no character reachable within the walk budget")

    _request(client, campaign_id, cell_id)
    assert _visual(client, campaign_id, cell_id).json()["dirty"] is False
    calls_before = fake.call_count

    # Attack until the band changes or the target dies.
    for _ in range(8):
        _turn(client, campaign_id, f"attack {target['name']}")
        if _visual(client, campaign_id, cell_id).json()["dirty"]:
            break

    assert _visual(client, campaign_id, cell_id).json()["dirty"] is True
    assert fake.call_count == calls_before, "damage alone must not render anything"


def test_updating_a_dirty_room_uses_the_edit_path(visuals_on, client):
    fake = visuals_on
    campaign_id, _ = _create(client, seed=42)
    cell_id, target = _walk_to_a_character(client, campaign_id)
    if target is None:
        pytest.skip("no character reachable within the walk budget")

    _request(client, campaign_id, cell_id)
    for _ in range(8):
        _turn(client, campaign_id, f"attack {target['name']}")
        if _visual(client, campaign_id, cell_id).json()["dirty"]:
            break
    if not _visual(client, campaign_id, cell_id).json()["dirty"]:
        pytest.skip("could not move the target across a band")

    _request(client, campaign_id, cell_id)

    status = _visual(client, campaign_id, cell_id).json()
    assert status["status"] == "READY"
    assert status["revision"] == 2
    assert status["dirty"] is False
    # The edit must have been given the previous image to work from.
    assert fake.calls[-1]["references"] == 1
    assert "Edit the reference image" in fake.calls[-1]["prompt"]
