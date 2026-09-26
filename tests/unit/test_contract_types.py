"""Unit tests for the shared domain/model contracts (TDD §8, §10)."""

from enum import StrEnum
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.domain import types as t


CELL_LOCATION = {"kind": "CELL", "ref_id": "cell_1_2", "slot": None}
INVENTORY_LOCATION = {"kind": "INVENTORY", "ref_id": "player_1", "slot": None}

MODEL_SAMPLES: dict[type[BaseModel], dict[str, Any]] = {
    t.Location: CELL_LOCATION,
    t.ActionIntent: {
        "action_type": "MOVE",
        "actor_id": "player_1",
        "targets": [],
        "params": {"direction": "NORTH"},
    },
    t.Check: {"kind": "SKILL", "suggested_difficulty": 14, "approach_modifier": 1},
    t.TransferItem: {
        "type": "TRANSFER_ITEM",
        "item_id": "item_1",
        "from_loc": CELL_LOCATION,
        "to_loc": INVENTORY_LOCATION,
        "quantity": 1,
    },
    t.ConsumeItem: {"type": "CONSUME_ITEM", "item_id": "item_1", "quantity": 1},
    t.SetFeatureState: {
        "type": "SET_FEATURE_STATE",
        "feature_id": "feat_cell_1_2_1",
        "key": "open_state",
        "value": "open",
    },
    t.CreateFeature: {
        "type": "CREATE_FEATURE",
        "kind": "barricade",
        "name": "rough chair barricade",
        "properties": ["movable", "breakable"],
        "state": {"condition": "intact"},
    },
    t.SetDisposition: {
        "type": "SET_DISPOSITION",
        "entity_id": "npc_1",
        "direction": "IMPROVE",
    },
    t.AdjustStat: {"type": "ADJUST_STAT", "entity_id": "enemy_1", "stat": "hp", "delta": -2},
    t.MoveEntity: {"type": "MOVE_ENTITY", "entity_id": "player_1", "to_cell": "cell_1_2"},
    t.SetStat: {"type": "SET_STAT", "entity_id": "player_1", "stat": "mp", "value": 4},
    t.CreateEntity: {"type": "CREATE_ENTITY", "entity_type": "NPC", "payload": {"name": "Mara"}},
    t.AddNpcKnowledge: {"type": "ADD_NPC_KNOWLEDGE", "entity_id": "npc_1", "fact_id": "fact_1"},
    t.SetQuestState: {"type": "SET_QUEST_STATE", "quest_id": "quest_1", "status": "ACTIVE"},
    t.SetBossDoorState: {"type": "SET_BOSS_DOOR_STATE", "unlocked": True},
    t.MarkCellRumored: {"type": "MARK_CELL_RUMORED", "cell_id": "cell_6_6"},
    t.Noop: {"type": "NOOP"},
    t.ActionProposal: {
        "action_type": "PERSUADE",
        "actor_id": "player_1",
        "targets": ["npc_1"],
        "feasibility": "REQUIRES_CHECK",
        "reason": "The NPC may be convinced.",
        "check": {"kind": "PERSUADE", "suggested_difficulty": 12, "approach_modifier": 1},
        "proposed_effects_on_success": [
            {"type": "SET_DISPOSITION", "entity_id": "npc_1", "direction": "IMPROVE"}
        ],
        "proposed_effects_on_failure": [{"type": "NOOP"}],
        "utterance": "Please help me.",
    },
    t.Claim: {"entity_id": "npc_1", "attribute": "present", "value": True},
    t.NarrationResult: {
        "prose": "Mara watches you from beside the chair.",
        "claims": [{"entity_id": "npc_1", "attribute": "present", "value": True}],
    },
    t.StaticEnvironment: {
        "materials": ["dark stone"],
        "lighting": "dim",
        "smell": "damp earth",
        "architectural_notes": "A low vaulted ceiling.",
    },
    t.FeatureDressing: {
        "slot_id": None,
        "kind": "torch sconce",
        "name": "blackened torch sconce",
        "properties": ["light_source"],
        "initial_state": {"light_state": "lit"},
    },
    t.EntityDressing: {
        "slot_id": "entity_1",
        "name": "Mara",
        "description": "A wary archivist.",
        "persona": "Precise and guarded.",
        "traits": ["observant"],
    },
    t.ItemDressing: {
        "slot_id": "item_1",
        "name": "iron key",
        "description": "A heavy, rust-flecked key.",
    },
    t.RoomDressing: {
        "room_name": "The Moss Crypt",
        "static_environment": {
            "materials": ["dark stone"],
            "lighting": "dim",
            "smell": "damp earth",
            "architectural_notes": "A low vaulted ceiling.",
        },
        "features": [
            {
                "slot_id": None,
                "kind": "rubble",
                "name": "mossy rubble",
                "properties": ["heavy"],
                "initial_state": {"condition": "broken"},
            },
            {
                "slot_id": "feature_container_1",
                "kind": "chest",
                "name": "iron-banded chest",
                "properties": ["container", "heavy"],
                "initial_state": {"open_state": "closed", "lock_state": "unlocked"},
            },
        ],
        "entities": [],
        "items": [],
    },
    t.EntitySlot: {"slot_id": "entity_1", "role": "NPC"},
    t.ItemSlot: {
        "slot_id": "item_1",
        "subtype_hint": "KEY",
        "placement": "CONTAINER",
        "container_slot_id": "feature_container_1",
        "holder_slot_id": None,
        "guard_slot_id": None,
    },
    t.Fact: {
        "fact_id": "fact_1",
        "type": "CELL_HINT",
        "subject_cell_id": "cell_6_6",
        "hint": "The sealed vault lies beyond the flooded hall.",
    },
    t.RoomPlan: {
        "cell_key": "cell_1_2",
        "archetype": "NPC_WITH_ITEM",
        "tier": 2,
        "entity_slots": [{"slot_id": "entity_1", "role": "NPC"}],
        "item_slots": [
            {
                "slot_id": "item_1",
                "subtype_hint": "TRINKET",
                "placement": "FLOOR",
                "container_slot_id": None,
                "holder_slot_id": None,
                "guard_slot_id": None,
            }
        ],
        "feature_range": [2, 5],
        "knowledge_facts": [],
    },
    t.TurnRequest: {"turn_id": "turn-1", "player_id": "player_1", "input": "north"},
    t.Event: {
        "campaign_id": "cmp_test000001",
        "event_id": "evt_1_0",
        "turn_sequence": 1,
        "event_index": 0,
        "turn_id": "turn-1",
        "type": "PLAYER_MOVED",
        "actor_id": "player_1",
        "entity_ids": ["player_1"],
        "cell_id": "cell_1_2",
        "payload": {"from_cell": "cell_1_1"},
        "summary": "Player moved north.",
        "memory_status": "NOT_REQUIRED",
        "memory_attempts": 0,
        "schema_version": 1,
    },
    t.TurnResult: {
        "turn_id": "turn-1",
        "turn_sequence": 1,
        "accepted": True,
        "reason": None,
        "current_cell_id": "cell_1_2",
        "events": [],
        "outcome_summary": "Moved north.",
    },
}


ENUM_TYPES: list[type[StrEnum]] = [
    t.ActionType,
    t.EntityType,
    t.LocationKind,
    t.CharacterStatus,
    t.EventType,
    t.MemoryStatus,
    t.Feasibility,
    t.CheckKind,
    t.ClaimAttribute,
    t.DispositionState,
    t.DispositionDirection,
    t.FeatureProperty,
    t.FeatureStateKey,
    t.Archetype,
    t.CampaignStatus,
    t.GenerationStatus,
    t.ItemSubtype,
    t.DangerTierLabel,
    t.RoomEntityRole,
    t.ItemSlotPlacement,
]


@pytest.mark.parametrize(("model", "sample"), MODEL_SAMPLES.items())
def test_models_round_trip_json_and_forbid_extra(
    model: type[BaseModel], sample: dict[str, Any]
) -> None:
    instance = model.model_validate(sample)
    dumped = instance.model_dump(mode="json")
    assert model.model_validate(dumped) == instance

    with pytest.raises(ValidationError):
        model.model_validate({**sample, "unexpected_field": "rejected"})


@pytest.mark.parametrize("enum_type", ENUM_TYPES)
def test_enums_accept_members_and_reject_unknown_values(enum_type: type[StrEnum]) -> None:
    first_member = next(iter(enum_type))
    assert enum_type(first_member.value) is first_member

    with pytest.raises(ValueError):
        enum_type("NOT_A_REAL_VALUE")


@pytest.mark.parametrize(
    "document",
    [
        {
            "type": "TRANSFER_ITEM",
            "item_id": "item_1",
            "from_loc": CELL_LOCATION,
            "to_loc": INVENTORY_LOCATION,
            "quantity": 1,
        },
        {"type": "NOOP"},
        {"type": "SET_DISPOSITION", "entity_id": "npc_1", "direction": "WORSEN"},
    ],
)
def test_effect_union_round_trips_by_discriminator(document: dict[str, Any]) -> None:
    adapter = TypeAdapter(t.Effect)
    effect = adapter.validate_python(document)
    assert adapter.validate_json(adapter.dump_json(effect)) == effect


def test_effect_union_rejects_unknown_variant_and_variant_fields() -> None:
    adapter = TypeAdapter(t.Effect)

    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "TELEPORT", "entity_id": "player_1"})

    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "NOOP", "entity_id": "player_1"})


def test_model_output_nullable_fields_are_required() -> None:
    proposal = MODEL_SAMPLES[t.ActionProposal]
    without_check = {key: value for key, value in proposal.items() if key != "check"}
    with pytest.raises(ValidationError):
        t.ActionProposal.model_validate(without_check)

    feature = MODEL_SAMPLES[t.FeatureDressing]
    without_slot = {key: value for key, value in feature.items() if key != "slot_id"}
    with pytest.raises(ValidationError):
        t.FeatureDressing.model_validate(without_slot)


def test_contract_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        t.AdjustStat(type="ADJUST_STAT", entity_id="enemy_1", stat="hp", delta=1)

    with pytest.raises(ValidationError):
        t.RoomPlan.model_validate({**MODEL_SAMPLES[t.RoomPlan], "tier": 6})

    with pytest.raises(ValidationError):
        t.StaticEnvironment(
            materials=[], lighting="dim", smell="damp", architectural_notes="vaulted"
        )
