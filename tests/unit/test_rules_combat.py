from types import SimpleNamespace

from app.domain.rules import resolve_world_action
from app.domain.types import ActionIntent, ActionType
from app.services.turn_orchestrator import _debug_murder_query


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


def test_debug_murder_query_reads_the_target_name():
    assert _debug_murder_query("murder goblin") == "goblin"
    assert _debug_murder_query("Murder the tunnel goblin") == "tunnel goblin"
    assert _debug_murder_query("attack goblin") is None
    assert _debug_murder_query("murder") is None
    assert _debug_murder_query("reanimate goblin") is None


def test_debug_murder_kills_a_creature_by_partial_name():
    view = snapshot(enemy_hp=100)
    view.characters[1]["name"] = "tunnel goblin"
    view.characters[1]["character"]["dodge_pct"] = 100
    result = resolve_world_action(
        ActionIntent(action_type=ActionType.ATTACK, actor_id="player",
                     params={"query": "goblin", "debug_murder": True}),
        view, turn_id="murder")
    assert result.accepted
    assert [event.type.value for event in result.events] == [
        "ATTACK_RESOLVED", "ENTITY_DIED", "XP_GAINED"
    ]
    assert result.events[0].summary == "You slay tunnel goblin."
    assert result.events[0].payload["hp_after"] == 0
    assert result.events[0].payload["damage"] == 100
    enemy = next(mutation for mutation in result.mutations
                 if mutation.document_id == "enemy")
    assert enemy.set_fields["character"]["status"] == "DEAD"
    assert enemy.set_fields["character"]["hp"] == 0


def test_debug_murder_rejects_a_missing_or_ambiguous_name():
    missing = resolve_world_action(
        ActionIntent(action_type=ActionType.ATTACK, actor_id="player",
                     params={"query": "dragon", "debug_murder": True}),
        snapshot(), turn_id="murder-missing")
    assert not missing.accepted
    assert missing.reason == "There's nothing here by that name to murder."

    view = snapshot()
    view.characters[1]["name"] = "tunnel goblin"
    other = {
        "entity_id": "enemy_2", "entity_type": "ENEMY", "name": "cave goblin", "version": 0,
        "location": {"kind": "CELL", "ref_id": "cell_0_0", "slot": None},
        "character": dict(view.characters[1]["character"]),
    }
    view.characters = (*view.characters, other)
    ambiguous = resolve_world_action(
        ActionIntent(action_type=ActionType.ATTACK, actor_id="player",
                     params={"query": "goblin", "debug_murder": True}),
        view, turn_id="murder-both")
    assert not ambiguous.accepted
    assert ambiguous.reason == "More than one creature matches that name."


def test_debug_reanimate_restores_a_fallen_creature_to_full_health():
    view = snapshot(enemy_hp=40)
    view.characters[1]["name"] = "tunnel goblin"
    view.characters[1]["character"]["hp"] = 0
    view.characters[1]["character"]["status"] = "DEAD"
    result = resolve_world_action(
        ActionIntent(action_type=ActionType.WAIT, actor_id="player",
                     params={"query": "goblin", "debug_reanimate": True}),
        view, turn_id="rise")
    assert result.accepted
    assert [event.type.value for event in result.events] == ["ENTITY_REANIMATED"]
    assert result.events[0].summary == "tunnel goblin rises, restored to full health."
    assert result.events[0].payload["hp_after"] == 40
    enemy = next(mutation for mutation in result.mutations
                 if mutation.document_id == "enemy")
    assert enemy.set_fields["character"]["status"] == "ALIVE"
    assert enemy.set_fields["character"]["hp"] == 40


def test_debug_reanimate_rejects_the_living_the_missing_and_the_ambiguous():
    living = resolve_world_action(
        ActionIntent(action_type=ActionType.WAIT, actor_id="player",
                     params={"query": "goblin", "debug_reanimate": True}),
        snapshot(), turn_id="rise-living")
    assert not living.accepted
    assert living.reason == "goblin is already alive."

    missing = resolve_world_action(
        ActionIntent(action_type=ActionType.WAIT, actor_id="player",
                     params={"query": "dragon", "debug_reanimate": True}),
        snapshot(), turn_id="rise-missing")
    assert not missing.accepted
    assert missing.reason == "There's no fallen creature here by that name."

    view = snapshot()
    view.characters[1]["name"] = "tunnel goblin"
    view.characters[1]["character"]["hp"] = 0
    view.characters[1]["character"]["status"] = "DEAD"
    other = {
        "entity_id": "enemy_2", "entity_type": "ENEMY", "name": "cave goblin", "version": 0,
        "location": {"kind": "CELL", "ref_id": "cell_0_0", "slot": None},
        "character": dict(view.characters[1]["character"]),
    }
    view.characters = (*view.characters, other)
    ambiguous = resolve_world_action(
        ActionIntent(action_type=ActionType.WAIT, actor_id="player",
                     params={"query": "goblin", "debug_reanimate": True}),
        view, turn_id="rise-both")
    assert not ambiguous.accepted
    assert ambiguous.reason == "More than one fallen creature matches that name."


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


def test_death_summaries_name_the_killer_and_the_dropped_item():
    view = snapshot()
    view.player["character"]["hp"] = 1
    enemy = view.characters[1]
    enemy["name"] = "tunnel goblin"
    enemy["character"]["attack"] = 20
    view.items = ({
        "entity_id": "item_5_4_1",
        "entity_type": "ITEM",
        "name": "brass key",
        "version": 0,
        "location": {"kind": "INVENTORY", "ref_id": "player", "slot": None},
        "item": {"quantity": 1, "max_stack": 1, "stackable": False,
                 "guarded_by": [], "status": "ACTIVE", "quest_critical": False},
    },)
    result = resolve_world_action(
        ActionIntent(action_type=ActionType.WAIT, actor_id="player"),
        view, turn_id="die")
    summaries = " ".join(event.summary for event in result.events)
    assert "tunnel goblin struck you for" in summaries
    assert "You are slain by tunnel goblin." in summaries
    assert "You drop brass key." in summaries
    assert "enemy" not in summaries
    assert "item_5_4_1" not in summaries


def test_talk_to_an_enemy_by_id_is_dialogue():
    """Adjudicated talk names the creature by id. An enemy is a valid target."""
    view = snapshot()
    view.characters[1]["name"] = "tunnel goblin"
    view.characters[1]["character"]["knowledge"] = [{
        "fact_id": "fact_1", "type": "CELL_HINT", "subject_cell_id": "cell_1_0",
        "hint": "Eastward.", "revealed_to": [],
    }]
    result = resolve_world_action(
        ActionIntent(action_type=ActionType.TALK, actor_id="player", targets=["enemy"],
                     params={"utterance": "ask tunnel goblin his name"}),
        view, turn_id="talk-enemy")
    assert result.accepted
    kinds = [event.type.value for event in result.events]
    assert kinds[0] == "DIALOGUE"
    assert result.events[0].payload["npc_id"] == "enemy"
    assert result.events[0].payload["utterance"] == "ask tunnel goblin his name"
    assert "FACT_REVEALED" not in kinds
    assert "ATTACK_RESOLVED" in kinds


def test_talk_to_nobody_is_still_rejected():
    result = resolve_world_action(
        ActionIntent(action_type=ActionType.TALK, actor_id="player",
                     params={"query": "dragon"}),
        snapshot(), turn_id="talk-missing")
    assert not result.accepted
    assert result.reason == "There's no one here by that name to speak with."


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
