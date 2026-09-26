"""Tracer bullet: the smallest end-to-end turn slice.

Proves the four-layer architecture: parse -> resolve -> commit -> read-back,
entirely against mongomock (no Atlas, no network).

The slice: a player at cell_0_0 moves north into cell_0_1 and the move sticks.
"""

import mongomock
from pymongo.database import Database

from app.domain.parser import parse_fast_path
from app.domain.rules import resolve_action
from app.domain.types import ActionType
from app.persistence.repositories import Repository
from app.world.topology import MINIMAL_TOPOLOGY

CAMPAIGN_ID = "cmp_tracer0001"
PLAYER_ID = "player_1"


def setup_minimal_world(db: Database) -> None:
    """Seed a campaign, its two cells, and one player at cell_0_0."""
    db.campaigns.insert_one(
        {
            "_id": CAMPAIGN_ID,
            "schema_version": 1,
            "topology": MINIMAL_TOPOLOGY.to_dict(),
            "current_turn": 0,
        }
    )
    db.cells.insert_many(
        [
            {
                "_id": f"{CAMPAIGN_ID}:cell_0_0",
                "campaign_id": CAMPAIGN_ID,
                "cell_id": "cell_0_0",
                "visited_by": [],
                "version": 0,
            },
            {
                "_id": f"{CAMPAIGN_ID}:cell_0_1",
                "campaign_id": CAMPAIGN_ID,
                "cell_id": "cell_0_1",
                "visited_by": [PLAYER_ID],
                "version": 0,
            },
        ]
    )
    db.entities.insert_one(
        {
            "_id": f"{CAMPAIGN_ID}:{PLAYER_ID}",
            "campaign_id": CAMPAIGN_ID,
            "schema_version": 1,
            "entity_id": PLAYER_ID,
            "entity_type": "PLAYER",
            "location": {"kind": "CELL", "ref_id": "cell_0_0"},
            "player": {
                "discovered_cell_ids": ["cell_0_0"],
                "new_cells_since_death": 0,
            },
            "version": 0,
        }
    )


def immediate_transaction(callback):
    return callback(None)


def test_move_north_commits_location_and_event():
    client = mongomock.MongoClient()
    db = client.dungeon
    repo = Repository(db, transaction_runner=immediate_transaction)
    setup_minimal_world(db)

    # 1. Parse the command.
    intent = parse_fast_path("north", PLAYER_ID)
    assert intent is not None
    assert intent.action_type is ActionType.MOVE
    assert intent.params["direction"] == "NORTH"

    # 2. Resolve against the world.
    resolution = resolve_action(
        intent,
        topology=MINIMAL_TOPOLOGY,
        current_cell_id="cell_0_0",
        campaign_id=CAMPAIGN_ID,
        turn_sequence=1,
        turn_id="turn-00000000-0000-0000-0000-000000000001",
    )
    assert resolution.accepted is True

    # 3. Commit.
    repo.commit_turn(CAMPAIGN_ID, PLAYER_ID, resolution)

    # 4. Read back and verify state + history.
    player = repo.get_entity(CAMPAIGN_ID, PLAYER_ID)
    assert player is not None
    assert player["location"]["ref_id"] == "cell_0_1"

    events = list(db.events.find({"campaign_id": CAMPAIGN_ID}))
    assert len(events) == 1
    assert events[0]["type"] == "PLAYER_MOVED"
    assert events[0]["payload"]["from_cell"] == "cell_0_0"
    assert events[0]["payload"]["to_cell"] == "cell_0_1"

    campaign = repo.get_campaign(CAMPAIGN_ID)
    assert campaign["current_turn"] == 1


def test_move_into_wall_is_rejected():
    client = mongomock.MongoClient()
    db = client.dungeon
    repo = Repository(db, transaction_runner=immediate_transaction)
    setup_minimal_world(db)

    # Moving east from cell_0_0 has no adjacency entry -> blocked.
    intent = parse_fast_path("east", PLAYER_ID)
    assert intent is not None

    resolution = resolve_action(
        intent,
        topology=MINIMAL_TOPOLOGY,
        current_cell_id="cell_0_0",
        campaign_id=CAMPAIGN_ID,
        turn_sequence=1,
        turn_id="turn-00000000-0000-0000-0000-000000000002",
    )
    assert resolution.accepted is False
    assert "wall" in resolution.reason.lower()

    # No commit happened, so nothing changed.
    player = repo.get_entity(CAMPAIGN_ID, PLAYER_ID)
    assert player["location"]["ref_id"] == "cell_0_0"
    assert db.events.count_documents({"campaign_id": CAMPAIGN_ID}) == 0
