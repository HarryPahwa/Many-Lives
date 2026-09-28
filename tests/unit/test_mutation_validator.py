from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import pytest

from app.domain.mutation_validator import (
    apply_mutation_bundle,
    validate_candidate_bundle,
)
from app.domain.mutations import (
    AppendEvent,
    MoveEntity,
    MutateAttribute,
    MutationBundle,
    TransferEntity,
)
from app.domain.types import EventType, LocationKind


@dataclass
class MockWorldSnapshot:
    campaign: Mapping[str, Any]
    player: Mapping[str, Any]
    current_cell: Mapping[str, Any]
    destination_cell: Mapping[str, Any] | None
    characters: Sequence[Mapping[str, Any]]
    items: Sequence[Mapping[str, Any]]
    container_items: Sequence[Mapping[str, Any]]
    owned_items: Sequence[Mapping[str, Any]]


@pytest.fixture
def sample_world():
    return MockWorldSnapshot(
        campaign={"id": "camp_1", "turn_count": 5, "version": 1},
        player={"id": "player_1", "version": 2, "stats": {"hp": 20}},
        current_cell={"id": "cell_0_0", "version": 1},
        destination_cell={"id": "cell_0_1", "version": 1},
        characters=[
            {"id": "goblin_1", "name": "Tunnel Goblin", "version": 1, "stats": {"hp": 8}}
        ],
        items=[
            {
                "id": "dagger_1",
                "name": "Iron Dagger",
                "version": 1,
                "location_kind": "CELL",
                "location_id": "cell_0_0",
            }
        ],
        container_items=[],
        owned_items=[],
    )


def test_validate_valid_candidate(sample_world):
    bundle = MutationBundle(
        bundle_id="cand_1",
        action_description="Strike goblin",
        rationale="Melee hit",
        draft_narration="You strike the goblin cleanly.",
        mutations=[
            MutateAttribute(target_id="goblin_1", path="stats.hp", value=3, op="SET"),
            AppendEvent(
                event_type=EventType.ATTACK_RESOLVED,
                payload={"damage": 5},
                summary="Goblin took 5 damage",
            ),
        ],
    )
    valid, reason = validate_candidate_bundle(sample_world, bundle)
    assert valid is True
    assert reason is None


def test_validate_hallucinated_entity_rejected(sample_world):
    bundle = MutationBundle(
        bundle_id="cand_hallucinated",
        action_description="Strike dragon",
        rationale="Imaginary dragon",
        draft_narration="You strike the dragon.",
        mutations=[
            MutateAttribute(
                target_id="dragon_boss_99", path="stats.hp", value=0, op="SET"
            ),
        ],
    )
    valid, reason = validate_candidate_bundle(sample_world, bundle)
    assert valid is False
    assert "not found in room context" in reason


def test_validate_protected_attribute_rejected(sample_world):
    bundle = MutationBundle(
        bundle_id="cand_exploit",
        action_description="Overwrite id",
        rationale="Hack",
        draft_narration="Hacking...",
        mutations=[
            MutateAttribute(target_id="player_1", path="campaign_id", value="camp_evil"),
        ],
    )
    valid, reason = validate_candidate_bundle(sample_world, bundle)
    assert valid is False
    assert "protected path" in reason


def test_validate_invalid_cell_move_rejected(sample_world):
    bundle = MutationBundle(
        bundle_id="cand_teleport",
        action_description="Teleport far",
        rationale="Warp",
        draft_narration="Warping far away.",
        mutations=[
            MoveEntity(entity_id="player_1", target_cell_id="cell_9_9"),
        ],
    )
    valid, reason = validate_candidate_bundle(sample_world, bundle)
    assert valid is False
    assert "not accessible" in reason


def test_apply_valid_mutation_bundle(sample_world):
    bundle = MutationBundle(
        bundle_id="cand_take",
        action_description="Take dagger",
        rationale="Looting item",
        draft_narration="You take the iron dagger.",
        mutations=[
            TransferEntity(
                entity_id="dagger_1",
                from_ref="cell_0_0",
                to_ref="player_1",
                location_kind=LocationKind.INVENTORY,
            ),
            AppendEvent(
                event_type=EventType.ITEM_TRANSFERRED,
                payload={"item_id": "dagger_1"},
                summary="Player picked up dagger",
            ),
        ],
    )
    resolution = apply_mutation_bundle(sample_world, bundle, turn_id="turn_5")
    assert resolution.accepted is True
    assert len(resolution.mutations) == 1
    assert resolution.mutations[0].document_id == "dagger_1"
    assert resolution.mutations[0].set_fields["location_kind"] == "INVENTORY"
    assert len(resolution.events) == 1
    assert resolution.events[0].type == EventType.ITEM_TRANSFERRED


def test_condition_mutation_validation_and_application(sample_world):
    # Valid ADD of physical condition
    bundle_add = MutationBundle(
        bundle_id="cand_bleed",
        action_description="Slash goblin",
        rationale="Bleeding strike",
        draft_narration="You slash the goblin, opening a bleeding wound.",
        mutations=[
            MutateAttribute(
                target_id="goblin_1",
                path="physical_conditions",
                value="BLEEDING",
                op="ADD",
            ),
            AppendEvent(
                event_type=EventType.ATTACK_RESOLVED,
                payload={"target_id": "goblin_1", "condition": "BLEEDING"},
                summary="Goblin is bleeding",
            ),
        ],
    )
    valid, reason = validate_candidate_bundle(sample_world, bundle_add)
    assert valid is True
    assert reason is None

    res = apply_mutation_bundle(sample_world, bundle_add)
    assert res.accepted is True
    assert res.mutations[0].set_fields == {"physical_conditions": ["BLEEDING"]}

    # Reject SET op for conditions
    bundle_set = MutationBundle(
        bundle_id="cand_bad_set",
        action_description="Reset conditions",
        rationale="Exploit",
        draft_narration="Conditions reset.",
        mutations=[
            MutateAttribute(
                target_id="goblin_1",
                path="physical_conditions",
                value=["BLEEDING"],
                op="SET",
            ),
        ],
    )
    valid, reason = validate_candidate_bundle(sample_world, bundle_set)
    assert valid is False
    assert "only ADD or REMOVE are permitted" in reason

    # Reject invalid condition value
    bundle_invalid_val = MutationBundle(
        bundle_id="cand_bad_val",
        action_description="Hex",
        rationale="Hexing",
        draft_narration="Hexed.",
        mutations=[
            MutateAttribute(
                target_id="goblin_1",
                path="mental_conditions",
                value="NONEXISTENT_MADNESS",
                op="ADD",
            ),
        ],
    )
    valid, reason = validate_candidate_bundle(sample_world, bundle_invalid_val)
    assert valid is False
    assert "Invalid MentalCondition" in reason
