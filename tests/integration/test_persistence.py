"""§20.2 integration tests 1–4 and 6 — persistence across a restart.

These are the tests the demo rests on: the claim is that continuity comes from
the store, not from a transcript. Each one plays some turns, drops every
in-process object (a fresh app, a fresh client, a fresh engine handle), and
then asserts the world is unchanged.

Against the in-memory stub the restart assertions **skip with a reason**
rather than passing vacuously — an in-process dict cannot demonstrate
durability, and a green tick here would be a lie. They light up as soon as
`get_engine()` returns a durable engine.

The pre-restart halves always run, because "damage an enemy, leave, return,
HP unchanged" is a real behavioural assertion at any level of durability.
"""

from __future__ import annotations

import importlib
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}


def _stubs():
    """Always the *current* stubs module.

    `restart_process()` reloads it, which rebinds the module object. Anything
    holding a module-level `from ... import` would then be talking to a dead
    copy, so every access here goes through sys.modules.
    """
    return importlib.import_module("app.services.stubs")


@pytest.fixture(autouse=True)
def _fresh_world():
    get_settings.cache_clear()
    _stubs().reset_stubs()
    yield
    # A restart test may have left reloaded module objects in place; reload
    # once more so the next test starts from a known, shared world.
    _stubs().reset_stubs()


def _client() -> TestClient:
    return TestClient(importlib.import_module("app.main").app)


def restart_process() -> TestClient:
    """Simulate the demo's `kill the server; start it again` step (§29.2).

    Reloads the seam module *and* the app, so the engine object, its caches,
    and the FastAPI app are all rebuilt from scratch. Anything that lived only
    in this process is genuinely gone afterwards — which is why the stub fails
    these assertions and a database-backed engine does not.
    """
    import app.services.stubs

    importlib.reload(app.services.stubs)

    import app.services.turn_orchestrator

    importlib.reload(app.services.turn_orchestrator)

    import app.api.routes_campaigns
    import app.api.routes_debug
    import app.api.routes_turns

    for module in (
        app.api.routes_campaigns,
        app.api.routes_turns,
        app.api.routes_debug,
    ):
        importlib.reload(module)

    import app.main

    importlib.reload(app.main)
    return TestClient(app.main.app)


def _create(client: TestClient, seed: int, name: str = "Ada") -> str:
    return client.post(
        "/api/campaigns", json={"player_name": name, "seed": seed}
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


def _walk_to_a_room_with(client: TestClient, campaign_id: str, predicate, steps=20):
    """Walk without backtracking until `predicate(visible_cell)` holds."""
    seen: set[str] = set()
    last: str | None = None
    for _ in range(steps):
        view = _turn(client, campaign_id, "look")
        cell = view["visible_cell"]
        if predicate(cell):
            return cell
        seen.add(cell["cell_id"])
        _, sx, sy = cell["cell_id"].split("_")
        x, y = int(sx), int(sy)
        offsets = {"north": (0, 1), "south": (0, -1), "east": (1, 0), "west": (-1, 0)}
        back = OPPOSITE.get(last) if last else None
        options = [d for d in cell["exits"] if d != back] or cell["exits"]
        if not options:
            return None
        fresh = [
            d
            for d in options
            if f"cell_{x + offsets[d][0]}_{y + offsets[d][1]}" not in seen
        ]
        last = (fresh or options)[0]
        _turn(client, campaign_id, last)
    return None


# ---------------------------------------------------------------------------
# §20.2 #1 — a room persists; a second entry does not call the dresser
# ---------------------------------------------------------------------------


def test_room_persists_on_revisit_and_is_not_redressed():
    client = _client()
    campaign_id = _create(client, seed=42)

    here = _turn(client, campaign_id, "look")["visible_cell"]
    direction = here["exits"][0]
    first = _turn(client, campaign_id, direction)["visible_cell"]

    _turn(client, campaign_id, OPPOSITE[direction])
    again = _turn(client, campaign_id, direction)["visible_cell"]

    # §5.5 / rule 9: identity is immutable after first generation.
    assert again["cell_id"] == first["cell_id"]
    assert again["name"] == first["name"]
    assert again["description"] == first["description"]
    assert [c["id"] for c in again["characters"]] == [
        c["id"] for c in first["characters"]
    ]
    assert [i["id"] for i in again["items"]] == [i["id"] for i in first["items"]]


def test_room_survives_a_restart(require_durable):
    """§20.2 #1, across a process boundary."""
    client = _client()
    campaign_id = _create(client, seed=42)
    direction = _turn(client, campaign_id, "look")["visible_cell"]["exits"][0]
    first = _turn(client, campaign_id, direction)["visible_cell"]

    client = restart_process()

    resumed = _resume(client, campaign_id)
    assert resumed["visible_cell"]["cell_id"] == first["cell_id"]
    assert resumed["visible_cell"]["name"] == first["name"]
    assert resumed["visible_cell"]["description"] == first["description"]


# ---------------------------------------------------------------------------
# §20.2 #2 — damage an enemy, leave, return, HP unchanged
# ---------------------------------------------------------------------------


def test_enemy_damage_survives_leaving_and_returning():
    client = _client()
    campaign_id = _create(client, seed=42)

    cell = _walk_to_a_room_with(
        client,
        campaign_id,
        lambda c: any(ch["status"] == "ALIVE" for ch in c["characters"]),
    )
    assert cell is not None, "the walk should have found a character"
    target = next(c for c in cell["characters"] if c["status"] == "ALIVE")

    hit = _turn(client, campaign_id, f"attack {target['name']}")
    assert hit["accepted"] is True, hit["reason"]
    after_hit = next(
        c for c in hit["visible_cell"]["characters"] if c["id"] == target["id"]
    )

    direction = hit["visible_cell"]["exits"][0]
    _turn(client, campaign_id, direction)
    back = _turn(client, campaign_id, OPPOSITE[direction])

    returned = next(
        c for c in back["visible_cell"]["characters"] if c["id"] == target["id"]
    )
    assert returned["status"] == after_hit["status"]
    # Damage persists: a struck NPC does not heal by being left alone (§4.6).
    assert returned["disposition"] == after_hit["disposition"]


def test_enemy_damage_survives_a_restart(require_durable):
    """§20.2 #2, across a process boundary."""
    client = _client()
    campaign_id = _create(client, seed=42)
    cell = _walk_to_a_room_with(
        client,
        campaign_id,
        lambda c: any(ch["status"] == "ALIVE" for ch in c["characters"]),
    )
    assert cell is not None
    target = next(c for c in cell["characters"] if c["status"] == "ALIVE")
    hit = _turn(client, campaign_id, f"attack {target['name']}")
    expected = next(
        c for c in hit["visible_cell"]["characters"] if c["id"] == target["id"]
    )

    client = restart_process()

    resumed = _resume(client, campaign_id)
    found = next(
        c for c in resumed["visible_cell"]["characters"] if c["id"] == target["id"]
    )
    assert found["status"] == expected["status"]


# ---------------------------------------------------------------------------
# §20.2 #3 — kill an enemy, restart, the corpse persists
# ---------------------------------------------------------------------------


def test_a_killed_enemy_stays_dead():
    client = _client()
    campaign_id = _create(client, seed=42)
    cell = _walk_to_a_room_with(
        client,
        campaign_id,
        lambda c: any(ch["status"] == "ALIVE" for ch in c["characters"]),
    )
    assert cell is not None
    target = next(c for c in cell["characters"] if c["status"] == "ALIVE")

    for _ in range(8):
        result = _turn(client, campaign_id, f"attack {target['name']}")
        found = next(
            (c for c in result["visible_cell"]["characters"] if c["id"] == target["id"]),
            None,
        )
        if found and found["status"] == "DEAD":
            break
    else:
        pytest.fail("the target never died within the attack budget")

    # A corpse is still in the room and is not attackable again.
    again = _turn(client, campaign_id, f"attack {target['name']}")
    assert again["accepted"] is False, "a dead entity must not be a valid target"

    look = _turn(client, campaign_id, "look")
    corpse = next(
        c for c in look["visible_cell"]["characters"] if c["id"] == target["id"]
    )
    assert corpse["status"] == "DEAD"


def test_a_killed_enemy_stays_dead_after_a_restart(require_durable):
    """§20.2 #3."""
    client = _client()
    campaign_id = _create(client, seed=42)
    cell = _walk_to_a_room_with(
        client,
        campaign_id,
        lambda c: any(ch["status"] == "ALIVE" for ch in c["characters"]),
    )
    assert cell is not None
    target = next(c for c in cell["characters"] if c["status"] == "ALIVE")
    for _ in range(8):
        result = _turn(client, campaign_id, f"attack {target['name']}")
        found = next(
            (c for c in result["visible_cell"]["characters"] if c["id"] == target["id"]),
            None,
        )
        if found and found["status"] == "DEAD":
            break

    client = restart_process()

    resumed = _resume(client, campaign_id)
    corpse = next(
        c for c in resumed["visible_cell"]["characters"] if c["id"] == target["id"]
    )
    assert corpse["status"] == "DEAD", "the dead must not be resurrected by a restart"


# ---------------------------------------------------------------------------
# §20.2 #4 — a moved item does not respawn at its source
# ---------------------------------------------------------------------------


def test_a_taken_item_does_not_respawn_in_its_room():
    client = _client()
    # Seed 9: the only exit from spawn is north, into a room holding a key.
    campaign_id = _create(client, seed=9)
    room = _turn(client, campaign_id, "north")["visible_cell"]
    item = room["items"][0]

    taken = _turn(client, campaign_id, f"take {item['name']}")
    assert taken["accepted"] is True, taken["reason"]

    # Leave and come back: the floor is still bare.
    _turn(client, campaign_id, "south")
    back = _turn(client, campaign_id, "north")
    assert item["id"] not in {i["id"] for i in back["visible_cell"]["items"]}

    sheet = client.get(f"/api/campaigns/{campaign_id}/player").json()
    assert item["id"] in {i["id"] for i in sheet["carried"]}


def test_a_taken_item_does_not_respawn_after_a_restart(require_durable):
    """§20.2 #4."""
    client = _client()
    campaign_id = _create(client, seed=9)
    room = _turn(client, campaign_id, "north")["visible_cell"]
    item = room["items"][0]
    _turn(client, campaign_id, f"take {item['name']}")

    client = restart_process()

    resumed = _resume(client, campaign_id)
    assert item["id"] not in {i["id"] for i in resumed["visible_cell"]["items"]}
    sheet = client.get(f"/api/campaigns/{campaign_id}/player").json()
    assert item["id"] in {i["id"] for i in sheet["carried"]}


# ---------------------------------------------------------------------------
# §20.2 #6 — duplicate turn_id applies once (also across a restart)
# ---------------------------------------------------------------------------


def test_duplicate_turn_id_applies_once():
    client = _client()
    campaign_id = _create(client, seed=42)
    direction = _turn(client, campaign_id, "look")["visible_cell"]["exits"][0]
    turn_id = str(uuid.uuid4())

    body = {"turn_id": turn_id, "player_id": "player_1", "input": direction}
    first = client.post(f"/api/campaigns/{campaign_id}/turns", json=body).json()
    second = client.post(f"/api/campaigns/{campaign_id}/turns", json=body).json()

    assert first == second
    assert client.get(f"/api/campaigns/{campaign_id}").json()["current_turn"] == (
        first["turn_sequence"]
    )


def test_duplicate_turn_id_applies_once_across_a_restart(require_durable):
    """§20.2 #6 — the client retries after the server came back."""
    client = _client()
    campaign_id = _create(client, seed=42)
    direction = _turn(client, campaign_id, "look")["visible_cell"]["exits"][0]
    turn_id = str(uuid.uuid4())
    body = {"turn_id": turn_id, "player_id": "player_1", "input": direction}
    first = client.post(f"/api/campaigns/{campaign_id}/turns", json=body).json()

    client = restart_process()

    replay = client.post(f"/api/campaigns/{campaign_id}/turns", json=body).json()
    assert replay["turn_sequence"] == first["turn_sequence"]
    assert client.get(f"/api/campaigns/{campaign_id}").json()["current_turn"] == (
        first["turn_sequence"]
    ), "a retry after a restart must not apply the turn a second time"


# ---------------------------------------------------------------------------
# Resume semantics (§7.4) — no transcript, no mutation
# ---------------------------------------------------------------------------


def test_resume_is_repeatable_and_never_advances_the_campaign():
    client = _client()
    campaign_id = _create(client, seed=42)
    _turn(client, campaign_id, "look")
    before = client.get(f"/api/campaigns/{campaign_id}").json()["current_turn"]

    first = _resume(client, campaign_id)
    second = _resume(client, campaign_id)

    after = client.get(f"/api/campaigns/{campaign_id}").json()["current_turn"]
    assert after == before
    assert first["visible_cell"] == second["visible_cell"]
    assert first["player"] == second["player"]


def test_resume_reports_the_map_and_player_consistently():
    client = _client()
    campaign_id = _create(client, seed=42)
    direction = _turn(client, campaign_id, "look")["visible_cell"]["exits"][0]
    _turn(client, campaign_id, direction)

    resumed = _resume(client, campaign_id)
    sheet = client.get(f"/api/campaigns/{campaign_id}/player").json()

    assert resumed["player"]["cell_id"] == sheet["cell_id"]
    assert resumed["map"]["player_cell"] == sheet["cell_id"]
    assert resumed["visible_cell"]["cell_id"] == sheet["cell_id"]
    assert resumed["manifest"] is not None, "the inspector needs a resume manifest"


def test_resume_of_an_unknown_campaign_is_404():
    client = _client()
    response = client.post(
        "/api/campaigns/cmp_missing/resume", json={"player_id": "player_1"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_stub_engine_is_honest_about_not_being_durable():
    """Guards the skip logic itself: a false DURABLE would hide real failures.

    Verified by hand this session: flipping `StubEngine.DURABLE = True` makes
    `test_room_survives_a_restart` fail (the resumed campaign 404s), proving
    the restart simulation really does drop in-process state and that these
    tests are not vacuous.
    """
    assert _stubs().get_engine().DURABLE is False
