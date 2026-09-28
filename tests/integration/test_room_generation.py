from app.persistence.sqlite import get_sqlite_connection

from app.domain.types import (
    EntityDressing,
    FeatureDressing,
    FeatureProperty,
    ItemDressing,
    RoomDressing,
    StaticEnvironment,
)
from app.persistence.indexes import create_btree_indexes
from app.persistence.repositories import Repository
from app.services.campaign_service import create_campaign
from app.services.room_service import generate_room


CAMPAIGN_ID = "cmp_roomtest0001"


class ImmediateTransactions:
    def __call__(self, callback):
        return callback(None)


class FakeDresser:
    def __init__(self, invalid_calls=0):
        self.calls = 0
        self.invalid_calls = invalid_calls

    def __call__(self, plan, errors):
        self.calls += 1
        features = []
        for slot in plan.item_slots:
            if slot.container_slot_id:
                required = (FeatureProperty.CONTAINER if slot.placement.value == "CONTAINER"
                            else FeatureProperty.CONCEALING)
                features.append(FeatureDressing(
                    slot_id=slot.container_slot_id, kind="alcove",
                    name=f"marked alcove {len(features) + 1}", properties=[required],
                    initial_state={"open_state": "open"} if required == FeatureProperty.CONTAINER else {},
                ))
        while len(features) < plan.feature_range[0]:
            features.append(FeatureDressing(
                slot_id=None, kind="pillar", name=f"carved pillar {len(features) + 1}",
                properties=[], initial_state={},
            ))
        dressing = RoomDressing(
            room_name="Quiet Test Chamber",
            static_environment=StaticEnvironment(materials=["stone"], lighting="dim",
                                                 smell="dust", architectural_notes="Low arches."),
            features=features,
            entities=[EntityDressing(
                slot_id=slot.slot_id, name=f"wanderer {index}", description="A dungeon wanderer.",
                persona="Careful and observant." if slot.role.value == "NPC" else None,
                traits=["wary"],
            ) for index, slot in enumerate(plan.entity_slots, start=1)],
            items=[ItemDressing(slot_id=slot.slot_id, name=f"relic {index}",
                                description="An old dungeon relic.")
                   for index, slot in enumerate(plan.item_slots, start=1)],
        )
        if self.calls <= self.invalid_calls:
            return dressing.model_copy(update={"items": []})
        return dressing


def repository():
    db = get_sqlite_connection(":memory:")
    create_btree_indexes(db)
    return db, Repository(db, transaction_runner=ImmediateTransactions())


def test_spawn_and_adjacent_room_persist_without_regeneration():
    db, repo = repository()
    dresser = FakeDresser()
    created = create_campaign(repo, "Ada", seed=9, campaign_id=CAMPAIGN_ID,
                              dress_room=dresser)
    assert db.cells.find_one({"campaign_id": CAMPAIGN_ID,
                              "cell_id": created.spawn_cell_id})["generated"] is True
    assert dresser.calls == 1
    campaign = db.campaigns.find_one({"_id": CAMPAIGN_ID})
    destination = campaign["topology"][created.spawn_cell_id][0]
    first = generate_room(repo, CAMPAIGN_ID, destination, dresser)
    calls = dresser.calls
    second = generate_room(repo, CAMPAIGN_ID, destination, dresser)
    assert first.cell["room"] == second.cell["room"]
    assert first.entity_ids == second.entity_ids
    assert dresser.calls == calls
    assert db.events.count_documents({"campaign_id": CAMPAIGN_ID,
                                      "type": "CELL_GENERATED"}) == 2


def test_invalid_dressing_retries_twice_then_uses_fallback():
    _db, repo = repository()
    create_campaign(repo, "Ada", seed=10, campaign_id=CAMPAIGN_ID)
    campaign = repo.get_campaign(CAMPAIGN_ID)
    destination = campaign["topology"][campaign["spawn_cell_id"]][0]
    dresser = FakeDresser(invalid_calls=99)
    result = generate_room(repo, CAMPAIGN_ID, destination, dresser)
    assert dresser.calls == 3
    assert result.generation_source == "FALLBACK"


def test_reserved_key_identity_is_retained_when_room_generates():
    db, repo = repository()
    create_campaign(repo, "Ada", seed=11, campaign_id=CAMPAIGN_ID)
    key = db.entities.find_one({"campaign_id": CAMPAIGN_ID, "entity_id": "item_k1"})
    result = generate_room(repo, CAMPAIGN_ID, key["location"]["ref_id"])
    updated = db.entities.find_one({"campaign_id": CAMPAIGN_ID, "entity_id": "item_k1"})
    assert updated["_id"] == key["_id"]
    assert updated["location"]["kind"] != "RESERVED"
    assert updated["item"]["quest_critical"] is True
    assert "item_k1" in result.entity_ids
    assert db.entities.count_documents({"campaign_id": CAMPAIGN_ID,
                                        "entity_id": "item_k1"}) == 1
