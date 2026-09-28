from app.persistence.sqlite import get_sqlite_connection

from app.domain.rules import resolve_world_action
from app.domain.types import ActionIntent, ActionType
from app.persistence.indexes import create_btree_indexes
from app.persistence.repositories import Repository
from app.services.campaign_service import create_campaign


CAMPAIGN_ID = "cmp_a4persist001"


class ImmediateTransactions:
    def __call__(self, callback):
        return callback(None)


def setup():
    db = get_sqlite_connection(":memory:")
    create_btree_indexes(db)
    repo = Repository(db, transaction_runner=ImmediateTransactions())
    created = create_campaign(repo, "Ada", seed=81, campaign_id=CAMPAIGN_ID)
    return db, repo, created


def character(campaign_id, entity_id, cell_id, *, status="ALIVE", attack=3):
    return {
        "_id": f"{campaign_id}:{entity_id}", "campaign_id": campaign_id,
        "schema_version": 1, "entity_id": entity_id, "entity_type": "ENEMY",
        "name": entity_id, "description": "A persistent foe.",
        "location": {"kind": "CELL", "ref_id": cell_id, "slot": None},
        "origin_cell_id": cell_id,
        "character": {"level": 1, "xp": 0, "pending_level_ups": 0,
                      "hp": 0 if status == "DEAD" else 8, "max_hp": 8,
                      "mp": 0, "max_mp": 0, "attack": attack, "defense": 0,
                      "speed": 3, "dodge_pct": 0, "skill": 1, "status": status,
                      "faction": "HOSTILE", "persona": None, "traits": [],
                      "alerted": False, "assisting": False, "disposition": {},
                      "knowledge": []},
        "version": 0, "created_turn": 0, "updated_turn": 0,
    }


def test_lethal_environment_response_drops_item_and_respawns_atomically():
    db, repo, created = setup()
    destination = db.campaigns.find_one({"_id": CAMPAIGN_ID})["topology"][created.spawn_cell_id][0]
    db.entities.update_one(
        {"campaign_id": CAMPAIGN_ID, "entity_id": created.player_id},
        {"$set": {"location.ref_id": destination, "character.hp": 1}},
    )
    db.entities.insert_one(character(CAMPAIGN_ID, "enemy_lethal", destination, attack=20))
    carried = {
        "_id": f"{CAMPAIGN_ID}:item_carried", "campaign_id": CAMPAIGN_ID,
        "schema_version": 1, "entity_id": "item_carried", "entity_type": "ITEM",
        "name": "carried trinket", "description": "A trinket.",
        "location": {"kind": "INVENTORY", "ref_id": created.player_id, "slot": None},
        "item": {"subtype": "TRINKET", "tier": 1, "stackable": False,
                 "quantity": 1, "max_stack": 1, "quest_critical": False,
                 "properties": [], "attack_bonus": 0, "armor_bonus": 0,
                 "effects": [], "spell": None, "hidden": False,
                 "concealment_dc": None, "guarded_by": [], "status": "ACTIVE"},
        "version": 0, "created_turn": 0, "updated_turn": 0,
    }
    db.entities.insert_one(carried)
    repo.begin_turn(CAMPAIGN_ID, "turn-death", created.player_id, "wait")
    result = resolve_world_action(
        ActionIntent(action_type=ActionType.WAIT, actor_id=created.player_id),
        repo.load_world_view(CAMPAIGN_ID, created.player_id), turn_id="turn-death")
    repo.commit_turn(CAMPAIGN_ID, created.player_id, result)
    fresh = Repository(db, transaction_runner=ImmediateTransactions())
    player = fresh.get_entity(CAMPAIGN_ID, created.player_id)
    dropped = fresh.get_entity(CAMPAIGN_ID, "item_carried")
    assert player["location"]["ref_id"] == created.spawn_cell_id
    assert player["character"]["hp"] == player["character"]["max_hp"]
    assert player["player"]["deaths"] == 1
    assert dropped["location"]["ref_id"] == destination
    assert dropped["item"]["guarded_by"] == ["enemy_lethal"]
    assert [event["type"] for event in db.events.find({"turn_id": "turn-death"}).sort("event_index", 1)][-4:] == [
        "PLAYER_DIED", "ITEM_DROPPED", "XP_GAINED", "PLAYER_RESPAWNED"
    ]


def test_item_looted_from_corpse_does_not_respawn_after_reload():
    db, repo, created = setup()
    corpse = character(CAMPAIGN_ID, "enemy_corpse", created.spawn_cell_id, status="DEAD")
    loot = {
        "_id": f"{CAMPAIGN_ID}:item_loot", "campaign_id": CAMPAIGN_ID,
        "schema_version": 1, "entity_id": "item_loot", "entity_type": "ITEM",
        "name": "corpse token", "description": "A token.",
        "location": {"kind": "INVENTORY", "ref_id": "enemy_corpse", "slot": None},
        "item": {"subtype": "TRINKET", "tier": 1, "stackable": False,
                 "quantity": 1, "max_stack": 1, "quest_critical": False,
                 "hidden": False, "guarded_by": [], "status": "ACTIVE"},
        "version": 0, "created_turn": 0, "updated_turn": 0,
    }
    db.entities.insert_many([corpse, loot])
    repo.begin_turn(CAMPAIGN_ID, "turn-loot", created.player_id, "take corpse token")
    resolution = resolve_world_action(
        ActionIntent(action_type=ActionType.TAKE_ITEM, actor_id=created.player_id,
                     params={"query": "corpse token"}),
        repo.load_world_view(CAMPAIGN_ID, created.player_id), turn_id="turn-loot")
    assert resolution.accepted
    repo.commit_turn(CAMPAIGN_ID, created.player_id, resolution)
    fresh = Repository(db, transaction_runner=ImmediateTransactions())
    assert fresh.get_entity(CAMPAIGN_ID, "item_loot")["location"] == {
        "kind": "INVENTORY", "ref_id": created.player_id, "slot": None
    }
