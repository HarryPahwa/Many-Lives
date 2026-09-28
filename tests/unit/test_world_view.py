"""Tests for immutable, campaign-scoped world snapshots."""

from types import MappingProxyType

from app.persistence.sqlite import get_sqlite_connection
import pytest

from app.persistence.repositories import Repository, StateNotFoundError


CAMPAIGN_ID = "cmp_view00000001"
OTHER_CAMPAIGN_ID = "cmp_view00000002"


def immediate_transaction(callback):
    return callback(None)


def seed_view_data(db) -> None:
    db.campaigns.insert_many(
        [
            {
                "_id": CAMPAIGN_ID,
                "config": {"grid": {"width": 7, "height": 7}},
                "topology": {"cell_0_0": ["cell_0_1"]},
            },
            {"_id": OTHER_CAMPAIGN_ID, "config": {"marker": "other"}},
        ]
    )
    db.cells.insert_many(
        [
            {
                "_id": f"{CAMPAIGN_ID}:cell_0_0",
                "campaign_id": CAMPAIGN_ID,
                "cell_id": "cell_0_0",
                "features": [
                    {"feature_id": "feat_cell_0_0_1", "state": {"open_state": "open"}}
                ],
            },
            {
                "_id": f"{CAMPAIGN_ID}:cell_0_1",
                "campaign_id": CAMPAIGN_ID,
                "cell_id": "cell_0_1",
                "features": [],
            },
            {
                "_id": f"{OTHER_CAMPAIGN_ID}:cell_0_0",
                "campaign_id": OTHER_CAMPAIGN_ID,
                "cell_id": "cell_0_0",
                "features": [],
            },
        ]
    )
    db.entities.insert_many(
        [
            {
                "_id": f"{CAMPAIGN_ID}:player_1",
                "campaign_id": CAMPAIGN_ID,
                "entity_id": "player_1",
                "entity_type": "PLAYER",
                "location": {"kind": "CELL", "ref_id": "cell_0_0"},
            },
            {
                "_id": f"{CAMPAIGN_ID}:npc_1",
                "campaign_id": CAMPAIGN_ID,
                "entity_id": "npc_1",
                "entity_type": "NPC",
                "location": {"kind": "CELL", "ref_id": "cell_0_1"},
            },
            {
                "_id": f"{CAMPAIGN_ID}:item_floor",
                "campaign_id": CAMPAIGN_ID,
                "entity_id": "item_floor",
                "entity_type": "ITEM",
                "location": {"kind": "CELL", "ref_id": "cell_0_1"},
            },
            {
                "_id": f"{CAMPAIGN_ID}:item_chest",
                "campaign_id": CAMPAIGN_ID,
                "entity_id": "item_chest",
                "entity_type": "ITEM",
                "location": {"kind": "CONTAINER", "ref_id": "feat_cell_0_0_1"},
            },
            {
                "_id": f"{OTHER_CAMPAIGN_ID}:player_1",
                "campaign_id": OTHER_CAMPAIGN_ID,
                "entity_id": "player_1",
                "entity_type": "PLAYER",
                "location": {"kind": "CELL", "ref_id": "cell_0_0"},
                "marker": "must not leak",
            },
        ]
    )


def test_world_view_loads_current_destination_and_visible_entities() -> None:
    db = get_sqlite_connection(":memory:")
    seed_view_data(db)
    repository = Repository(db, transaction_runner=immediate_transaction)

    view = repository.load_world_view(CAMPAIGN_ID, "player_1", "cell_0_1")

    assert view.current_cell["cell_id"] == "cell_0_0"
    assert view.destination_cell is not None
    assert view.destination_cell["cell_id"] == "cell_0_1"
    assert {entity["entity_id"] for entity in view.characters} == {"player_1", "npc_1"}
    assert [item["entity_id"] for item in view.items] == ["item_floor"]
    assert [item["entity_id"] for item in view.container_items] == ["item_chest"]
    assert view.config["grid"]["width"] == 7
    assert all(entity["campaign_id"] == CAMPAIGN_ID for entity in view.characters)


def test_world_view_is_recursively_immutable_and_detached() -> None:
    db = get_sqlite_connection(":memory:")
    seed_view_data(db)
    repository = Repository(db, transaction_runner=immediate_transaction)
    view = repository.load_world_view(CAMPAIGN_ID, "player_1")

    assert isinstance(view.campaign, MappingProxyType)
    with pytest.raises(TypeError):
        view.campaign["status"] = "WON"  # type: ignore[index]
    with pytest.raises(TypeError):
        view.config["grid"]["width"] = 99  # type: ignore[index]

    db.campaigns.update_one({"_id": CAMPAIGN_ID}, {"$set": {"config.grid.width": 99}})
    assert view.config["grid"]["width"] == 7


def test_world_view_optional_destination_is_none() -> None:
    db = get_sqlite_connection(":memory:")
    seed_view_data(db)
    repository = Repository(db, transaction_runner=immediate_transaction)
    assert repository.load_world_view(CAMPAIGN_ID, "player_1").destination_cell is None


@pytest.mark.parametrize(
    ("campaign_id", "player_id", "destination", "message"),
    [
        ("cmp_missing00000", "player_1", None, "Campaign"),
        (CAMPAIGN_ID, "missing_player", None, "Player"),
        (CAMPAIGN_ID, "player_1", "cell_9_9", "Destination"),
    ],
)
def test_world_view_missing_state_is_explicit(
    campaign_id: str, player_id: str, destination: str | None, message: str
) -> None:
    db = get_sqlite_connection(":memory:")
    seed_view_data(db)
    repository = Repository(db, transaction_runner=immediate_transaction)
    with pytest.raises(StateNotFoundError, match=message):
        repository.load_world_view(campaign_id, player_id, destination)
