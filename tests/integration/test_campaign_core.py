"""Offline A2 integration: create a campaign and move through empty cells."""

from copy import deepcopy

import mongomock
import pytest
from pymongo.errors import DuplicateKeyError

from app.domain.parser import parse_fast_path
from app.domain.rules import resolve_action
from app.persistence.indexes import create_btree_indexes
from app.persistence.repositories import Repository
from app.services.campaign_service import create_campaign
from app.world.topology import Topology, parse_cell_key


CAMPAIGN_ID = "cmp_test00000001"


class TransactionSpy:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, callback):
        self.calls += 1
        return callback(None)


def build_campaign(seed: int = 20260926):
    db = mongomock.MongoClient().dungeon
    create_btree_indexes(db)
    transactions = TransactionSpy()
    repository = Repository(db, transaction_runner=transactions)
    result = create_campaign(
        repository,
        "Ada",
        seed=seed,
        campaign_id=CAMPAIGN_ID,
    )
    return db, repository, transactions, result


def test_create_campaign_persists_complete_initial_world() -> None:
    db, _repository, transactions, result = build_campaign()

    assert transactions.calls == 2
    assert db.campaigns.count_documents({}) == 1
    assert db.cells.count_documents({"campaign_id": CAMPAIGN_ID}) == 49
    assert db.entities.count_documents({"campaign_id": CAMPAIGN_ID}) == 7
    assert db.events.count_documents({"campaign_id": CAMPAIGN_ID}) == 3

    campaign = db.campaigns.find_one({"_id": CAMPAIGN_ID})
    assert campaign["seed"] == result.seed
    assert campaign["status"] == "ACTIVE"
    assert len(campaign["topology"]) == 49
    assert campaign["spawn_cell_id"] == result.spawn_cell_id
    assert campaign["boss_cell_id"] == result.boss_cell_id
    assert campaign["boss_door"] == {
        "required_keys": 3,
        "submitted_key_ids": [],
        "unlocked": False,
    }

    cells = list(db.cells.find({"campaign_id": CAMPAIGN_ID}))
    spawn = next(cell for cell in cells if cell["cell_id"] == result.spawn_cell_id)
    assert spawn["generation_status"] == "GENERATED"
    assert spawn["generated"] is True
    assert spawn["room"]["archetype"] == "EMPTY"
    assert sum(cell["generation_status"] == "UNGENERATED" for cell in cells) == 48
    assert sum(cell["reservations"]["boss"] for cell in cells) == 1
    reserved_cells = [cell for cell in cells if cell["reservations"]["key_item_ids"]]
    assert len(reserved_cells) == 6
    assert result.spawn_cell_id not in {cell["cell_id"] for cell in reserved_cells}
    assert result.boss_cell_id not in {cell["cell_id"] for cell in reserved_cells}

    keys = list(db.entities.find({"campaign_id": CAMPAIGN_ID, "entity_type": "ITEM"}))
    assert len(keys) == 6
    assert len({key["location"]["ref_id"] for key in keys}) == 6
    assert all(key["location"]["kind"] == "RESERVED" for key in keys)
    assert all(key["item"]["subtype"] == "KEY" for key in keys)
    assert all(key["item"]["quest_critical"] is True for key in keys)

    player = db.entities.find_one(
        {"campaign_id": CAMPAIGN_ID, "entity_id": result.player_id}
    )
    assert player["location"]["ref_id"] == result.spawn_cell_id
    assert player["character"]["status"] == "ALIVE"
    assert player["character"]["hp"] == player["character"]["max_hp"] == 20
    assert player["player"]["discovered_cell_ids"] == [result.spawn_cell_id]

    events = list(db.events.find({"campaign_id": CAMPAIGN_ID}).sort("event_index", 1))
    assert [event["type"] for event in events] == [
        "CAMPAIGN_CREATED",
        "PLAYER_SPAWNED",
        "CELL_GENERATED",
    ]
    assert all(event["turn_sequence"] == 0 for event in events)


def test_fixed_seed_reproduces_all_mechanical_state() -> None:
    first_db, *_ = build_campaign(12345)
    second_db, *_ = build_campaign(12345)
    first = first_db.campaigns.find_one({"_id": CAMPAIGN_ID})
    second = second_db.campaigns.find_one({"_id": CAMPAIGN_ID})

    for document in (first, second):
        document.pop("created_at")
        document.pop("updated_at")
    assert first == second

    def normalized(collection, query):
        documents = list(collection.find(query).sort("_id", 1))
        return [deepcopy(document) for document in documents]

    assert normalized(first_db.cells, {}) == normalized(second_db.cells, {})
    assert normalized(first_db.entities, {}) == normalized(second_db.entities, {})


def test_duplicate_campaign_fails_without_overwriting_existing_state() -> None:
    db, repository, _transactions, _result = build_campaign()
    with pytest.raises(DuplicateKeyError):
        create_campaign(repository, "Grace", seed=7, campaign_id=CAMPAIGN_ID)
    assert db.campaigns.count_documents({}) == 1
    assert db.cells.count_documents({"campaign_id": CAMPAIGN_ID}) == 49


def test_campaign_can_move_to_an_adjacent_empty_cell() -> None:
    db, repository, transactions, result = build_campaign()
    campaign = db.campaigns.find_one({"_id": CAMPAIGN_ID})
    topology = Topology(campaign["topology"])
    destination = topology.neighbors(result.spawn_cell_id)[0]
    sx, sy = parse_cell_key(result.spawn_cell_id)
    dx, dy = parse_cell_key(destination)
    command = {
        (0, 1): "north",
        (0, -1): "south",
        (1, 0): "east",
        (-1, 0): "west",
    }[(dx - sx, dy - sy)]
    intent = parse_fast_path(command, result.player_id)
    assert intent is not None

    resolution = resolve_action(
        intent,
        topology=topology,
        current_cell_id=result.spawn_cell_id,
        campaign_id=CAMPAIGN_ID,
        turn_sequence=1,
        turn_id="turn-a2-1",
    )
    repository.commit_turn(CAMPAIGN_ID, result.player_id, resolution)

    assert transactions.calls == 3
    player = db.entities.find_one(
        {"campaign_id": CAMPAIGN_ID, "entity_id": result.player_id}
    )
    assert player["location"]["ref_id"] == destination
    assert destination in player["player"]["discovered_cell_ids"]
    assert player["player"]["new_cells_since_death"] == 1
    assert result.player_id in db.cells.find_one(
        {"campaign_id": CAMPAIGN_ID, "cell_id": destination}
    )["visited_by"]
    assert db.campaigns.find_one({"_id": CAMPAIGN_ID})["current_turn"] == 1
    assert db.events.count_documents({"campaign_id": CAMPAIGN_ID}) == 4


def test_revisit_does_not_increment_new_cell_counter() -> None:
    db, repository, _transactions, result = build_campaign()
    destination = db.campaigns.find_one({"_id": CAMPAIGN_ID})["topology"][
        result.spawn_cell_id
    ][0]
    db.entities.update_one(
        {"campaign_id": CAMPAIGN_ID, "entity_id": result.player_id},
        {
            "$set": {
                "location.ref_id": destination,
                "player.discovered_cell_ids": [result.spawn_cell_id, destination],
                "player.new_cells_since_death": 4,
            }
        },
    )
    topology = Topology(db.campaigns.find_one({"_id": CAMPAIGN_ID})["topology"])
    sx, sy = parse_cell_key(destination)
    dx, dy = parse_cell_key(result.spawn_cell_id)
    command = {(0, 1): "north", (0, -1): "south", (1, 0): "east", (-1, 0): "west"}[
        (dx - sx, dy - sy)
    ]
    intent = parse_fast_path(command, result.player_id)
    resolution = resolve_action(
        intent,
        topology,
        destination,
        CAMPAIGN_ID,
        1,
        "turn-a2-revisit",
    )
    repository.commit_turn(CAMPAIGN_ID, result.player_id, resolution)
    player = db.entities.find_one(
        {"campaign_id": CAMPAIGN_ID, "entity_id": result.player_id}
    )
    assert player["player"]["new_cells_since_death"] == 4
