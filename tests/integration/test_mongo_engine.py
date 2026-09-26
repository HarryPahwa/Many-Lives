"""MongoEngine exercises the production seam without an Atlas dependency."""

from __future__ import annotations

import mongomock

from app.api.schemas import TurnRequest
from app.persistence.indexes import create_btree_indexes
from app.persistence.repositories import Repository
from app.services.mongo_engine import MongoEngine
from app.services.stubs import StubHarness
from app.services.turn_orchestrator import TurnOrchestrator


class ImmediateTransactions:
    def __call__(self, callback):
        return callback(None)


def _engine(database) -> MongoEngine:
    return MongoEngine(Repository(database, transaction_runner=ImmediateTransactions()))


def test_mongo_engine_persists_world_and_replays_turns():
    database = mongomock.MongoClient().dungeon_test
    create_btree_indexes(database)
    engine = _engine(database)
    orchestrator = TurnOrchestrator(engine=engine, harness=StubHarness())

    campaign = engine.create_campaign("Ada", seed=42)
    first = orchestrator.take_turn(
        campaign.campaign_id,
        TurnRequest(turn_id="turn-look-0001", player_id="player_1", input="look"),
    )

    assert engine.DURABLE is True
    assert first.accepted is True
    assert database.turns.find_one(
        {"campaign_id": campaign.campaign_id, "turn_id": "turn-look-0001"}
    )["result"] == first.model_dump()

    direction = engine.load_world_view(campaign.campaign_id, "player_1").exits[0]
    moved = orchestrator.take_turn(
        campaign.campaign_id,
        TurnRequest(turn_id="turn-move-0001", player_id="player_1", input=direction),
    )
    game_map = engine.build_map(campaign.campaign_id, "player_1")

    assert moved.player.cell_id == game_map.player_cell
    assert any(cell.cell_id == moved.player.cell_id and cell.exits for cell in game_map.cells)

    restarted = _engine(database)
    replay = TurnOrchestrator(engine=restarted, harness=StubHarness()).take_turn(
        campaign.campaign_id,
        TurnRequest(turn_id="turn-look-0001", player_id="player_1", input="look"),
    )

    assert replay == first
    assert restarted.get_campaign(campaign.campaign_id).current_turn == moved.turn_sequence
    assert restarted.load_world_view(campaign.campaign_id, "player_1").player.cell_id == moved.player.cell_id


def test_mongo_engine_list_campaigns_skips_campaign_without_player():
    database = mongomock.MongoClient().dungeon_test
    engine = _engine(database)
    missing_player = engine.create_campaign("Ada", seed=42)
    valid_campaign = engine.create_campaign("Bea", seed=43)

    database.entities.delete_one(
        {"campaign_id": missing_player.campaign_id, "entity_id": missing_player.player_id}
    )

    assert [campaign.campaign_id for campaign in engine.list_campaigns()] == [
        valid_campaign.campaign_id
    ]
