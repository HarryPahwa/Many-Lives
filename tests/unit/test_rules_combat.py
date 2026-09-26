from types import SimpleNamespace

from app.domain.rules import resolve_world_action
from app.domain.types import ActionIntent, ActionType


def snapshot(*, enemy_hp=1):
    campaign = {"_id": "cmp_test00000001", "seed": 4, "current_turn": 0, "version": 0,
                "boss_cell_id": "cell_1_0", "boss_door": {"required_keys": 3,
                "submitted_key_ids": [], "unlocked": False}, "status": "ACTIVE",
                "winner_player_id": None, "topology": {"cell_0_0": ["cell_1_0"],
                                                       "cell_1_0": ["cell_0_0"]}}
    player = {"entity_id": "player", "entity_type": "PLAYER", "version": 0,
              "location": {"kind": "CELL", "ref_id": "cell_0_0", "slot": None},
              "character": {"level": 1, "xp": 0, "pending_level_ups": 0,
                            "hp": 20, "max_hp": 20, "mp": 6, "max_mp": 6,
                            "attack": 5, "defense": 2, "speed": 4, "dodge_pct": 0,
                            "skill": 3, "status": "ALIVE"},
              "player": {"spawn_cell_id": "cell_0_0", "discovered_cell_ids": ["cell_0_0"],
                         "new_cells_since_death": 0, "damage_dealt_since_death": 0,
                         "deaths": 0, "kills": 0}}
    enemy = {"entity_id": "enemy", "entity_type": "ENEMY", "name": "goblin", "version": 0,
             "location": {"kind": "CELL", "ref_id": "cell_0_0", "slot": None},
             "character": {"level": 1, "xp": 0, "pending_level_ups": 0,
                           "hp": enemy_hp, "max_hp": enemy_hp, "mp": 0, "max_mp": 0,
                           "attack": 2, "defense": 0, "speed": 3, "dodge_pct": 0,
                           "status": "ALIVE", "disposition": {}}}
    cell = {"cell_id": "cell_0_0", "version": 0, "visited_by": ["player"]}
    return SimpleNamespace(campaign=campaign, player=player, current_cell=cell,
                           destination_cell=None, characters=(player, enemy), items=(),
                           container_items=(), owned_items=())


def test_attack_kills_and_awards_xp_without_dead_enemy_response():
    intent = ActionIntent(action_type=ActionType.ATTACK, actor_id="player",
                          params={"query": "goblin"})
    result = resolve_world_action(intent, snapshot(), turn_id="turn-1")
    assert result.accepted
    assert [event.type.value for event in result.events] == [
        "ATTACK_RESOLVED", "ENTITY_DIED", "XP_GAINED"
    ]
    enemy_mutation = next(mutation for mutation in result.mutations
                          if mutation.document_id == "enemy")
    assert enemy_mutation.set_fields["character"]["status"] == "DEAD"
    assert result.expected_turn == 0 and result.expected_campaign_version == 0


def test_locked_boss_destination_rejects_move():
    view = snapshot()
    view.destination_cell = {"cell_id": "cell_1_0", "version": 0, "visited_by": []}
    intent = ActionIntent(action_type=ActionType.MOVE, actor_id="player",
                          params={"direction": "EAST"})
    result = resolve_world_action(intent, view, turn_id="turn-2")
    assert not result.accepted
    assert "boss door" in result.reason


def test_talk_reveals_allowed_fact_and_rumors_cell():
    view = snapshot()
    npc = view.characters[1]
    npc["entity_type"] = "NPC"
    npc["name"] = "keeper"
    npc["character"]["disposition"] = {
        "player": {"state": "NEUTRAL", "trust": 20, "reason_event_ids": []}
    }
    npc["character"]["knowledge"] = [{"fact_id": "fact_1", "type": "CELL_HINT",
                                         "subject_cell_id": "cell_1_0", "hint": "Eastward.",
                                         "revealed_to": []}]
    view.player["player"]["rumored_cell_ids"] = []
    result = resolve_world_action(
        ActionIntent(action_type=ActionType.TALK, actor_id="player",
                     params={"query": "keeper"}), view, turn_id="talk-1")
    assert result.accepted
    assert [event.type.value for event in result.events[:3]] == [
        "DIALOGUE", "FACT_REVEALED", "CELL_RUMORED"
    ]
    player_mutation = next(value for value in result.mutations if value.document_id == "player")
    assert "cell_1_0" in player_mutation.set_fields["player"]["rumored_cell_ids"]


def test_persuasion_records_check_and_updates_disposition():
    view = snapshot()
    npc = view.characters[1]
    npc["entity_type"] = "NPC"
    npc["name"] = "keeper"
    result = resolve_world_action(
        ActionIntent(action_type=ActionType.PERSUADE, actor_id="player",
                     params={"query": "keeper", "approach_modifier": 2}),
        view, turn_id="social-1")
    assert result.accepted
    assert result.events[0].type.value == "CHECK_RESOLVED"
    npc_mutation = next(value for value in result.mutations if value.document_id == "enemy")
    disposition = npc_mutation.set_fields["character"]["disposition"]["player"]
    assert -100 <= disposition["trust"] <= 100


def test_successful_steal_records_disposition_on_check_event():
    view = snapshot()
    npc = view.characters[1]
    npc["entity_type"] = "NPC"
    npc["name"] = "keeper"
    npc["character"]["speed"] = -20
    item = {
        "entity_id": "item_key",
        "entity_type": "ITEM",
        "name": "key",
        "version": 0,
        "location": {"kind": "INVENTORY", "ref_id": "enemy", "slot": None},
        "item": {"subtype": "KEY", "quantity": 1, "stackable": False},
    }
    view.owned_items = (item,)
    result = resolve_world_action(
        ActionIntent(action_type=ActionType.STEAL, actor_id="player",
                     params={"query": "key", "target_query": "keeper"}),
        view,
        turn_id="steal-1",
    )
    assert result.accepted
    assert result.events[0].type.value == "CHECK_RESOLVED"
    assert "disposition" in result.events[0].payload
    assert result.events[1].type.value == "ITEM_TRANSFERRED"
    assert "disposition" not in result.events[1].payload
    npc_mutation = next(value for value in result.mutations if value.document_id == "enemy")
    assert npc_mutation.set_fields["character"]["disposition"]["player"][
        "reason_event_ids"
    ] == [result.events[0].event_id]
