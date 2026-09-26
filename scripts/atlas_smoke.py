"""End-to-end Atlas smoke test (TDD §20.2, §29).

Proves the demo's core claim against the real sandbox:
create a campaign -> move -> reconnect with a fresh client -> state survives.

    uv run python scripts/atlas_smoke.py
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

from app.domain.parser import parse_fast_path
from app.domain.rules import resolve_world_action
from app.persistence.indexes import create_btree_indexes
from app.persistence.mongo import close_mongo_client, get_database
from app.persistence.repositories import Repository
from app.services.campaign_service import create_campaign
from app.world.topology import Topology, parse_cell_key


DIRECTION = {(0, -1): "north", (0, 1): "south", (1, 0): "east", (-1, 0): "west"}

_CLEANUP_COLLECTIONS = ("campaigns", "cells", "entities", "events", "turns")


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    load_dotenv()
    db = get_database()
    create_btree_indexes(db)
    repo = Repository(db)  # NOTE: default runner = real with_transaction

    seed = 20260926
    created = create_campaign(repo, "Ada", seed=seed)
    cid = created.campaign_id
    print(
        f"1. created campaign {cid} "
        f"(spawn={created.spawn_cell_id}, boss={created.boss_cell_id})"
    )

    # --- move one step into an adjacent cell ---
    campaign = repo.get_campaign(cid)
    dest = Topology(campaign["topology"]).neighbors(created.spawn_cell_id)[0]
    sx, sy = parse_cell_key(created.spawn_cell_id)
    dx, dy = parse_cell_key(dest)
    command = DIRECTION[(dx - sx, dy - sy)]

    intent = parse_fast_path(command, created.player_id)
    view = repo.load_world_view(cid, created.player_id, destination_cell_id=dest)
    resolution = resolve_world_action(intent, view, turn_id="atlas-smoke-turn-1")
    if not resolution.accepted:
        fail(f"move rejected: {resolution.reason}")
    repo.commit_turn(cid, created.player_id, resolution)

    player = repo.get_entity(cid, created.player_id)
    assert player["location"]["ref_id"] == dest
    campaign = repo.get_campaign(cid)
    events = list(
        db.events.find({"campaign_id": cid}).sort(
            [("turn_sequence", 1), ("event_index", 1)]
        )
    )
    print(
        f"2. moved {command} {created.spawn_cell_id} -> {dest}; "
        f"turn={campaign['current_turn']}; events={[e['type'] for e in events]}"
    )

    # --- the durability proof: a brand-new connection must see the same state ---
    close_mongo_client()
    fresh_db = get_database()
    fresh = Repository(fresh_db)
    player2 = fresh.get_entity(cid, created.player_id)
    campaign2 = fresh.get_campaign(cid)
    if player2["location"]["ref_id"] != dest or campaign2["current_turn"] != 1:
        fail("state did not survive a fresh connection")

    print(
        f"3. DURABILITY OK: fresh connection sees player at "
        f"{player2['location']['ref_id']}, turn {campaign2['current_turn']}"
    )

    # --- cleanup (only the smoke campaign) ---
    for collection in _CLEANUP_COLLECTIONS:
        fresh_db[collection].delete_many({"campaign_id": cid})
    print("4. cleaned up smoke campaign")

    close_mongo_client()
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()