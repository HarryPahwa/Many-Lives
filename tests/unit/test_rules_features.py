from app.domain.parser import parse_fast_path
from app.domain.rules import resolve_world_action
from app.domain.types import ActionIntent, ActionType
from tests.unit.test_rules_combat import snapshot


def feature_view():
    view = snapshot()
    view.characters[1]["character"]["status"] = "DEAD"
    view.current_cell["features"] = [
        {"feature_id": "feat_cell_0_0_1", "kind": "torch sconce",
         "name": "soot-blackened torch sconce", "properties": ["light_source"],
         "state": {"light_state": "lit"}, "created_by": "GENERATION"},
        {"feature_id": "feat_cell_0_0_2", "kind": "stone bench",
         "name": "weathered stone bench", "properties": ["heavy"],
         "state": {"condition": "intact"}, "created_by": "GENERATION"},
    ]
    return view


def interact(*effects, params=None):
    return ActionIntent(action_type=ActionType.INTERACT, actor_id="player",
                        targets=["feat_cell_0_0_1"], params=params or {},
                        effects_on_success=list(effects))


def unlit(feature_id="feat_cell_0_0_1"):
    return {"type": "SET_FEATURE_STATE", "feature_id": feature_id,
            "key": "light_state", "value": "unlit"}


def test_set_feature_state_mutates_cell_and_emits_event():
    result = resolve_world_action(interact(unlit()), feature_view(), turn_id="t1")
    assert result.accepted
    assert result.events[0].type.value == "FEATURE_STATE_CHANGED"
    assert result.events[0].payload == {"feature_id": "feat_cell_0_0_1", "key": "light_state",
                                        "before": "lit", "after": "unlit"}
    cell = next(m for m in result.mutations if m.collection == "cells")
    assert cell.document_id == "cell_0_0" and cell.expected_version == 0
    assert cell.set_fields["features"][0]["state"]["light_state"] == "unlit"


def test_repeat_state_is_rejected_with_player_facing_reason():
    view = feature_view()
    view.current_cell["features"][0]["state"]["light_state"] = "unlit"
    result = resolve_world_action(interact(unlit()), view, turn_id="t2")
    assert not result.accepted
    assert result.reason == "The soot-blackened torch sconce is already unlit."


def test_property_prerequisite_blocks_change():
    effect = {"type": "SET_FEATURE_STATE", "feature_id": "feat_cell_0_0_2",
              "key": "condition", "value": "broken"}
    result = resolve_world_action(interact(effect), feature_view(), turn_id="t3")
    assert not result.accepted
    assert "can't be made broken" in result.reason
    assert result.rejected_effects == [effect]


def test_interaction_without_effects_tells_player_nothing_changed():
    result = resolve_world_action(interact(), feature_view(), turn_id="t4")
    assert not result.accepted
    assert result.reason == "Nothing you do there changes anything."


def test_failed_check_commits_check_without_success_effects():
    view = feature_view()
    view.player["character"]["skill"] = -50
    params = {"check_kind": "SKILL", "suggested_difficulty": 18, "approach_modifier": 0}
    result = resolve_world_action(interact(unlit(), params=params), view, turn_id="t5")
    assert result.accepted
    assert [e.type.value for e in result.events] == ["CHECK_RESOLVED"]
    assert not any(m.collection == "cells" for m in result.mutations)


def test_seam_resolution_maps_check_rolls_to_wire_rolls():
    from app.services.mongo_engine import MongoEngine

    view = feature_view()
    params = {"check_kind": "SKILL", "suggested_difficulty": 10, "approach_modifier": 0}
    intent = interact(unlit(), params=params)
    raw = resolve_world_action(intent, view, turn_id="t7")
    seam = MongoEngine._seam_resolution(raw, pending=(view, intent))
    assert [(roll.purpose, roll.sides) for roll in seam.rolls] == [("check", 20)]


def test_create_feature_appends_player_feature():
    effect = {"type": "CREATE_FEATURE", "kind": "mark", "name": "scratched X",
              "properties": [], "state": {}}
    result = resolve_world_action(interact(effect), feature_view(), turn_id="t6")
    assert result.accepted
    cell = next(m for m in result.mutations if m.collection == "cells")
    created = cell.set_fields["features"][-1]
    assert created["feature_id"] == "feat_cell_0_0_3"
    assert created["created_by"] == "PLAYER_ACTION"


def test_adjudicated_talk_resolves_target_id_without_query():
    view = snapshot()
    npc = view.characters[1]
    npc["entity_type"] = "NPC"
    npc["name"] = "dust-worn keeper"
    result = resolve_world_action(
        ActionIntent(action_type=ActionType.TALK, actor_id="player", targets=["enemy"]),
        view, turn_id="talk-target")
    assert result.accepted
    assert result.events[0].type.value == "DIALOGUE"


def test_talk_utterance_reaches_narrator_social_context():
    from app.services import stubs  # noqa: F401 - import order avoids a known cycle
    from app.services.harness_adapter import _social_context
    from app.api.schemas import PlayerState, VisibleCell, VisibleCharacter

    view = snapshot()
    npc = view.characters[1]
    npc["entity_type"] = "NPC"
    npc["name"] = "dust-worn keeper"
    result = resolve_world_action(
        ActionIntent(action_type=ActionType.TALK, actor_id="player", targets=["enemy"],
                     params={"utterance": "ask keeper his name"}),
        view, turn_id="talk-social")
    assert result.events[0].payload["utterance"] == "ask keeper his name"

    world = stubs.WorldView(
        campaign_id="c", player_id="player", campaign_status="ACTIVE", current_turn=1,
        player=PlayerState(hp=20, max_hp=20, mp=6, max_mp=6, level=1, xp=0,
                           pending_level_ups=0, cell_id="cell_0_0"),
        visible_cell=VisibleCell(cell_id="cell_0_0", name="Archive", description="",
                                 exits=[], features=[], items=[],
                                 characters=[VisibleCharacter(id="enemy", name="dust-worn keeper",
                                                              status="ALIVE", disposition=None)]),
    )
    social = _social_context(world, result.events)
    assert social.npc_id == "enemy"
    assert social.persona == "dust-worn keeper"
    assert social.recent_dialogue == ["Player: ask keeper his name"]


def test_invalid_adjudicator_output_rejects_instead_of_raising():
    from app.services import stubs  # noqa: F401 - import order avoids a known cycle
    from app.api.schemas import PlayerState, VisibleCell
    from app.harness.model_client import ModelOutputError
    from app.services.harness_adapter import ProductionHarness

    class FailingClient:
        def structured(self, *args, **kwargs):
            raise ModelOutputError("Structured output failed for ADJUDICATOR after 3 attempts")

    world = stubs.WorldView(
        campaign_id="c", player_id="player", campaign_status="ACTIVE", current_turn=1,
        player=PlayerState(hp=20, max_hp=20, mp=6, max_mp=6, level=1, xp=0,
                           pending_level_ups=0, cell_id="cell_0_0"),
        visible_cell=VisibleCell(cell_id="cell_0_0", name="Archive", description="",
                                 exits=[], features=[], items=[], characters=[]),
    )
    harness = ProductionHarness(client=FailingClient())
    assert harness.adjudicate("smash it", "", world, "CREATIVE") == (None, [])


def test_compound_commands_go_to_the_adjudicator():
    assert parse_fast_path("talk to keeper and ask his name", "player") is None
    assert parse_fast_path("talk to keeper about the door", "player") is None
    assert parse_fast_path("talk to keeper", "player").params == {"query": "keeper"}


def test_open_fast_path_carries_open_state_effect():
    intent = parse_fast_path("open chest", "player")
    assert intent.params == {"query": "chest"}
    assert intent.effects_on_success == [{"type": "SET_FEATURE_STATE", "feature_id": "chest",
                                          "key": "open_state", "value": "open"}]
    assert parse_fast_path("open boss door", "player").effects_on_success == []
