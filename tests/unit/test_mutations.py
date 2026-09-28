import pytest
from pydantic import ValidationError

from app.domain.mutations import (
    AppendEvent,
    MoveEntity,
    MutateAttribute,
    MutationBundle,
    TransferEntity,
)
from app.domain.types import EventType, LocationKind


def test_mutate_attribute_valid():
    mutation = MutateAttribute(
        target_id="player_1",
        path="stats.hp",
        value=15,
        op="SET",
    )
    assert mutation.kind == "MUTATE_ATTRIBUTE"
    assert mutation.target_id == "player_1"
    assert mutation.path == "stats.hp"
    assert mutation.value == 15
    assert mutation.op == "SET"


def test_transfer_entity_valid():
    transfer = TransferEntity(
        entity_id="sword_1",
        from_ref="cell_0_0",
        to_ref="player_1",
        location_kind=LocationKind.INVENTORY,
    )
    assert transfer.kind == "TRANSFER_ENTITY"
    assert transfer.location_kind == LocationKind.INVENTORY


def test_move_entity_valid():
    move = MoveEntity(
        entity_id="player_1",
        target_cell_id="cell_0_1",
    )
    assert move.kind == "MOVE_ENTITY"
    assert move.target_cell_id == "cell_0_1"


def test_append_event_valid():
    event = AppendEvent(
        event_type=EventType.ATTACK_RESOLVED,
        payload={"damage": 5},
        summary="Player struck goblin for 5 damage",
    )
    assert event.kind == "APPEND_EVENT"
    assert event.event_type == EventType.ATTACK_RESOLVED


def test_mutation_bundle_composition():
    bundle = MutationBundle(
        bundle_id="cand_1",
        action_description="Attack the goblin",
        rationale="Swing iron sword at goblin",
        draft_narration="You swing your blade cleanly into the goblin's side.",
        mutations=[
            MutateAttribute(target_id="goblin_1", path="stats.hp", value=3, op="SET"),
            AppendEvent(
                event_type=EventType.ATTACK_RESOLVED,
                payload={"damage": 5},
                summary="Goblin took 5 damage",
            ),
        ],
    )
    assert len(bundle.mutations) == 2
    assert bundle.mutations[0].kind == "MUTATE_ATTRIBUTE"
    assert bundle.mutations[1].kind == "APPEND_EVENT"


def test_forbid_extra_fields():
    with pytest.raises(ValidationError):
        MutateAttribute(
            target_id="player_1",
            path="stats.hp",
            value=10,
            extra_field="invalid",  # type: ignore
        )
