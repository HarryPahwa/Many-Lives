"""C3: the fog-of-war map contract (§17.3) and the player sheet (§17.2).

These pin the server side of what the minimap and character panel draw. The
minimap derives walls from `exits`, so an asymmetric or over-shared map would
render a wrong dungeon — the tests below are written against that risk rather
than against the stub's internals.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.stubs import reset_stubs


def _app():
    """The current FastAPI app; see the note in test_turn_loop.py."""
    import importlib

    return importlib.import_module("app.main").app

OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
OFFSETS = {"north": (0, 1), "south": (0, -1), "east": (1, 0), "west": (-1, 0)}


@pytest.fixture(autouse=True)
def _fresh_world():
    get_settings.cache_clear()
    reset_stubs()
    yield
    reset_stubs()


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app())


def _create(client: TestClient) -> str:
    return client.post(
        "/api/campaigns", json={"player_name": "Ada", "seed": 42}
    ).json()["campaign"]["campaign_id"]


def _turn(client: TestClient, campaign_id: str, text: str):
    return client.post(
        f"/api/campaigns/{campaign_id}/turns",
        json={"turn_id": str(uuid.uuid4()), "player_id": "player_1", "input": text},
    ).json()


def _coords(cell_id: str) -> tuple[int, int]:
    _, x, y = cell_id.split("_")
    return int(x), int(y)


def _explore(
    client: TestClient,
    campaign_id: str,
    steps: int = 12,
    *,
    stop_on_item: bool = False,
) -> list[dict]:
    """Walk without immediately backtracking, preferring unvisited cells.

    Taking `exits[0]` every turn just ping-pongs between two rooms, which makes
    "nothing was found" meaningless. This visits genuinely new cells.
    """
    seen: set[str] = set()
    views: list[dict] = []
    last_direction: str | None = None

    for _ in range(steps):
        view = _turn(client, campaign_id, "look")
        views.append(view)
        cell = view["visible_cell"]
        seen.add(cell["cell_id"])
        if stop_on_item and cell["items"]:
            break
        x, y = _coords(cell["cell_id"])

        back = OPPOSITE.get(last_direction) if last_direction else None
        options = [d for d in cell["exits"] if d != back] or cell["exits"]
        if not options:
            break
        fresh = [
            d
            for d in options
            if f"cell_{x + OFFSETS[d][0]}_{y + OFFSETS[d][1]}" not in seen
        ]
        direction = (fresh or options)[0]
        _turn(client, campaign_id, direction)
        last_direction = direction

    return views


# ---------------------------------------------------------------------------
# Fog of war (§17.3)
# ---------------------------------------------------------------------------


def test_map_is_seven_by_seven_and_starts_almost_dark(client: TestClient):
    campaign_id = _create(client)
    body = client.get(f"/api/campaigns/{campaign_id}/map").json()

    assert body["width"] == 7 and body["height"] == 7
    spawn = body["player_cell"]
    # The fog is the absence of data: at most the spawn plus rumoured cells.
    discovered = [c for c in body["cells"] if c["state"] == "DISCOVERED"]
    assert [c["cell_id"] for c in discovered] == [spawn]
    assert len(body["cells"]) < 49


def test_undiscovered_cells_are_never_returned(client: TestClient):
    """The client must not be able to reconstruct the dungeon it hasn't seen."""
    campaign_id = _create(client)
    body = client.get(f"/api/campaigns/{campaign_id}/map").json()
    returned = {c["cell_id"] for c in body["cells"]}

    every_cell = {f"cell_{x}_{y}" for x in range(7) for y in range(7)}
    assert returned < every_cell
    for cell_id in every_cell - returned:
        assert cell_id not in str(body), f"{cell_id} leaked into the map response"


def test_rumored_cells_are_marked_and_carry_no_exits(client: TestClient):
    campaign_id = _create(client)
    body = client.get(f"/api/campaigns/{campaign_id}/map").json()

    rumored = [c for c in body["cells"] if c["state"] == "RUMORED"]
    for cell in rumored:
        # §17.3: exits are returned only for discovered cells.
        assert cell["exits"] == []
        assert cell["name"] is None
        assert cell["cell_id"] in body["rumored"]


def test_moving_discovers_the_destination_and_reveals_its_exits(client: TestClient):
    campaign_id = _create(client)
    first = _turn(client, campaign_id, "look")
    direction = first["visible_cell"]["exits"][0]

    before = client.get(f"/api/campaigns/{campaign_id}/map").json()
    moved = _turn(client, campaign_id, direction)
    after = client.get(f"/api/campaigns/{campaign_id}/map").json()

    new_id = moved["player"]["cell_id"]
    assert new_id not in {c["cell_id"] for c in before["cells"] if c["state"] == "DISCOVERED"}
    entry = next(c for c in after["cells"] if c["cell_id"] == new_id)
    assert entry["state"] == "DISCOVERED"
    assert entry["exits"], "a discovered cell must expose its exits for wall drawing"
    assert after["player_cell"] == new_id


def test_entering_a_rumored_cell_promotes_it_to_discovered(client: TestClient):
    """A cell must never be reported as both rumoured and discovered."""
    campaign_id = _create(client)
    for _ in range(12):
        body = client.get(f"/api/campaigns/{campaign_id}/map").json()
        states = {}
        for cell in body["cells"]:
            assert cell["cell_id"] not in states, "a cell appears twice in the map"
            states[cell["cell_id"]] = cell["state"]
        discovered = {k for k, v in states.items() if v == "DISCOVERED"}
        assert discovered.isdisjoint(set(body["rumored"]))
        view = _turn(client, campaign_id, "look")
        exits = view["visible_cell"]["exits"]
        if not exits:
            break
        _turn(client, campaign_id, exits[0])


def test_exits_are_symmetric_between_discovered_neighbours(client: TestClient):
    """If A opens north onto B, B must open south onto A, or walls lie."""
    campaign_id = _create(client)
    _explore(client, campaign_id, steps=12)

    body = client.get(f"/api/campaigns/{campaign_id}/map").json()
    known = {
        c["cell_id"]: c for c in body["cells"] if c["state"] == "DISCOVERED"
    }
    assert len(known) > 1, "the walk should have discovered several cells"

    for cell_id, cell in known.items():
        x, y = _coords(cell_id)
        for direction in cell["exits"]:
            dx, dy = OFFSETS[direction]
            neighbour_id = f"cell_{x + dx}_{y + dy}"
            neighbour = known.get(neighbour_id)
            if neighbour is None:
                continue  # not discovered yet; nothing to contradict
            assert OPPOSITE[direction] in neighbour["exits"], (
                f"{cell_id} exits {direction} to {neighbour_id}, "
                f"but {neighbour_id} has no {OPPOSITE[direction]} exit"
            )


def test_exits_never_point_off_the_grid(client: TestClient):
    campaign_id = _create(client)
    _explore(client, campaign_id, steps=12)

    body = client.get(f"/api/campaigns/{campaign_id}/map").json()
    for cell in body["cells"]:
        x, y = _coords(cell["cell_id"])
        assert 0 <= x < body["width"] and 0 <= y < body["height"]
        assert (cell["x"], cell["y"]) == (x, y)
        for direction in cell["exits"]:
            dx, dy = OFFSETS[direction]
            assert 0 <= x + dx < body["width"], f"{cell['cell_id']} exits the grid"
            assert 0 <= y + dy < body["height"], f"{cell['cell_id']} exits the grid"


def test_map_is_scoped_to_one_campaign(client: TestClient):
    """§5.10 / INV-13 — no cross-campaign leak through the map route."""
    first = _create(client)
    second = client.post(
        "/api/campaigns", json={"player_name": "Bo", "seed": 99}
    ).json()["campaign"]["campaign_id"]

    view = _turn(client, first, "look")
    _turn(client, first, view["visible_cell"]["exits"][0])

    a = client.get(f"/api/campaigns/{first}/map").json()
    b = client.get(f"/api/campaigns/{second}/map").json()
    b_discovered = [c for c in b["cells"] if c["state"] == "DISCOVERED"]
    assert len(a["cells"]) > len(b_discovered)
    assert [cell["cell_id"] for cell in b_discovered] == [b["player_cell"]]


# ---------------------------------------------------------------------------
# Player sheet (§17.2, §4.4, §4.9)
# ---------------------------------------------------------------------------


def test_player_sheet_reports_starting_stats(client: TestClient):
    campaign_id = _create(client)
    sheet = client.get(f"/api/campaigns/{campaign_id}/player").json()

    # §4.4 starting stats.
    assert (sheet["hp"], sheet["max_hp"]) == (20, 20)
    assert (sheet["mp"], sheet["max_mp"]) == (6, 6)
    assert sheet["stats"] == {
        "attack": 5,
        "defense": 2,
        "speed": 4,
        "dodge_pct": 10,
        "skill": 3,
    }
    assert sheet["level"] == 1
    # §4.9 inventory bounds.
    assert sheet["carried_slots"] == 6
    assert sheet["carried"] == []
    assert sheet["weapon"] is None and sheet["armor"] is None
    assert sheet["keys_required"] == 3


def test_player_sheet_cell_tracks_movement(client: TestClient):
    campaign_id = _create(client)
    view = _turn(client, campaign_id, "look")
    moved = _turn(client, campaign_id, view["visible_cell"]["exits"][0])

    sheet = client.get(f"/api/campaigns/{campaign_id}/player").json()
    assert sheet["cell_id"] == moved["player"]["cell_id"]
    assert client.get(f"/api/campaigns/{campaign_id}/map").json()["player_cell"] == (
        sheet["cell_id"]
    )


def test_dodge_never_exceeds_the_cap(client: TestClient):
    """INV-14 — dodge_pct <= 40 for players."""
    campaign_id = _create(client)
    sheet = client.get(f"/api/campaigns/{campaign_id}/player").json()
    assert sheet["stats"]["dodge_pct"] <= 40


def test_taking_an_item_moves_it_out_of_the_room(client: TestClient):
    """INV-01 / rule 8 — an item has exactly one location, never two."""
    campaign_id = client.post(
        "/api/campaigns", json={"player_name": "Ada", "seed": 9}
    ).json()["campaign"]["campaign_id"]

    views = _explore(client, campaign_id, steps=40, stop_on_item=True)
    cell = views[-1]["visible_cell"]
    assert cell["items"], f"seed 9 should put an item in {cell['cell_id']}"
    found = cell["items"][0]

    result = _turn(client, campaign_id, f"take {found['name']}")
    assert result["accepted"] is True, result["reason"]

    sheet = client.get(f"/api/campaigns/{campaign_id}/player").json()
    carried_ids = {i["id"] for i in sheet["carried"]}
    assert found["id"] in carried_ids

    after = _turn(client, campaign_id, "look")
    room_ids = {i["id"] for i in after["visible_cell"]["items"]}
    assert found["id"] not in room_ids, "the item is in two places at once"
    # Keys count toward the boss door; ordinary generated loot does not.
    expected_keys = int(found["name"].casefold().endswith("key"))
    assert sheet["keys_held"] == expected_keys
    assert all(not i["stackable"] for i in sheet["carried"] if "key" in i["name"])


def test_player_route_is_scoped_to_one_campaign(client: TestClient):
    first = _create(client)
    second = client.post(
        "/api/campaigns", json={"player_name": "Bo", "seed": 7}
    ).json()["campaign"]["campaign_id"]

    assert client.get(f"/api/campaigns/{first}/player").json()["name"] == "Ada"
    assert client.get(f"/api/campaigns/{second}/player").json()["name"] == "Bo"
