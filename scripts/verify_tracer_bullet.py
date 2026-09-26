"""Manual verification: run the slice and print the raw Mongo documents.

Self-contained (no imports from the test package). Usage:

    uv run python scripts/verify_tracer_bullet.py
"""

import mongomock
from pymongo.database import Database

from app.domain.parser import parse_fast_path
from app.domain.rules import resolve_action
from app.persistence.repositories import Repository
from app.world.topology import MINIMAL_TOPOLOGY

CAMPAIGN_ID = "cmp_tracer0001"
PLAYER_ID = "player_1"


def setup_minimal_world(db: Database) -> None:
    db.campaigns.insert_one(
        {
            "_id": CAMPAIGN_ID,
            "schema_version": 1,
            "topology": MINIMAL_TOPOLOGY.to_dict(),
            "current_turn": 0,
        }
    )
    db.entities.insert_one(
        {
            "_id": f"{CAMPAIGN_ID}:{PLAYER_ID}",
            "campaign_id": CAMPAIGN_ID,
            "schema_version": 1,
            "entity_id": PLAYER_ID,
            "entity_type": "PLAYER",
            "location": {"kind": "CELL", "ref_id": "cell_0_0"},
        }
    )


def main() -> None:
    client = mongomock.MongoClient()
    db = client.dungeon
    repo = Repository(db)
    setup_minimal_world(db)

    intent = parse_fast_path("north", PLAYER_ID)
    resolution = resolve_action(
        intent,
        topology=MINIMAL_TOPOLOGY,
        current_cell_id="cell_0_0",
        campaign_id=CAMPAIGN_ID,
        turn_sequence=1,
        turn_id="turn-00000000-0000-0000-0000-000000000001",
    )
    repo.commit_turn(CAMPAIGN_ID, PLAYER_ID, resolution)

    print("=== player entity (after commit) ===")
    print(repo.get_entity(CAMPAIGN_ID, PLAYER_ID))
    print("\n=== events ===")
    for e in db.events.find({"campaign_id": CAMPAIGN_ID}):
        print(e)
    print("\n=== campaign ===")
    print(repo.get_campaign(CAMPAIGN_ID))


if __name__ == "__main__":
    main()