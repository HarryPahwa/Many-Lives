from app.persistence.sqlite import get_sqlite_connection

from app.domain.rules import resolve_world_action
from app.domain.types import ActionIntent, ActionType
from app.persistence.indexes import create_btree_indexes
from app.persistence.repositories import Repository
from app.services.campaign_service import create_campaign


CAMPAIGN_ID = "cmp_a4tests00001"


class ImmediateTransactions:
    def __call__(self, callback):
        return callback(None)


def setup_enemy(*, hp=1):
    db = get_sqlite_connection(":memory:")
    create_btree_indexes(db)
    repo = Repository(db, transaction_runner=ImmediateTransactions())
    created = create_campaign(repo, "Ada", seed=44, campaign_id=CAMPAIGN_ID)
    enemy = {
        "_id": f"{CAMPAIGN_ID}:enemy_test", "campaign_id": CAMPAIGN_ID,
        "schema_version": 1, "entity_id": "enemy_test", "entity_type": "ENEMY",
        "name": "test goblin", "description": "A test foe.",
        "location": {"kind": "CELL", "ref_id": created.spawn_cell_id, "slot": None},
        "origin_cell_id": created.spawn_cell_id,
        "character": {"level": 1, "xp": 0, "pending_level_ups": 0,
                      "hp": hp, "max_hp": hp, "mp": 0, "max_mp": 0,
                      "attack": 2, "defense": 0, "speed": 3, "dodge_pct": 0,
                      "skill": 1, "status": "ALIVE", "faction": "HOSTILE",
                      "persona": None, "traits": [], "alerted": False,
                      "assisting": False, "disposition": {}, "knowledge": []},
        "version": 0, "created_turn": 0, "updated_turn": 0,
    }
    db.entities.insert_one(enemy)
    return db, repo, created


def test_duplicate_turn_replays_result_and_applies_kill_once():
    db, repo, created = setup_enemy()
    claim = repo.begin_turn(CAMPAIGN_ID, "turn-a4-1", created.player_id,
                            "attack test goblin")
    assert claim.state == "NEW"
    view = repo.load_world_view(CAMPAIGN_ID, created.player_id)
    resolution = resolve_world_action(
        ActionIntent(action_type=ActionType.ATTACK, actor_id=created.player_id,
                     params={"query": "test goblin"}),
        view,
        turn_id="turn-a4-1",
    )
    first = repo.commit_turn(CAMPAIGN_ID, created.player_id, resolution)
    second = repo.commit_turn(CAMPAIGN_ID, created.player_id, resolution)
    assert second == first
    assert db.campaigns.find_one({"_id": CAMPAIGN_ID})["current_turn"] == 1
    assert db.entities.find_one({"campaign_id": CAMPAIGN_ID,
                                 "entity_id": "enemy_test"})["character"]["status"] == "DEAD"
    assert db.events.count_documents({"campaign_id": CAMPAIGN_ID,
                                      "turn_id": "turn-a4-1"}) == 3
    assert db.turns.count_documents({"campaign_id": CAMPAIGN_ID,
                                     "turn_id": "turn-a4-1"}) == 1
    turn = db.turns.find_one({"campaign_id": CAMPAIGN_ID, "turn_id": "turn-a4-1"})
    assert turn["invariants"] == {"checked": 15, "failures": []}
    assert repo.check_campaign_invariants(CAMPAIGN_ID).passed


def test_enemy_damage_and_corpse_persist_through_fresh_repository():
    db, repo, created = setup_enemy(hp=20)
    repo.begin_turn(CAMPAIGN_ID, "turn-a4-2", created.player_id, "attack test goblin")
    resolution = resolve_world_action(
        ActionIntent(action_type=ActionType.ATTACK, actor_id=created.player_id,
                     params={"query": "test goblin"}),
        repo.load_world_view(CAMPAIGN_ID, created.player_id), turn_id="turn-a4-2")
    repo.commit_turn(CAMPAIGN_ID, created.player_id, resolution)
    hp = db.entities.find_one({"campaign_id": CAMPAIGN_ID,
                               "entity_id": "enemy_test"})["character"]["hp"]
    fresh = Repository(db, transaction_runner=ImmediateTransactions())
    assert fresh.get_entity(CAMPAIGN_ID, "enemy_test")["character"]["hp"] == hp < 20
