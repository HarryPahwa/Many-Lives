"""Parity and authorization checks for deterministic mutation compilation."""

from app.domain.mutation_adapter import resolution_to_mutation_bundle
from app.domain.mutation_validator import apply_mutation_bundle, validate_candidate_bundle
from app.domain.mutations import ApplyDocumentMutation, MutationBundle
from app.domain.rules import DocumentInsert, DocumentMutation, Resolution
from app.domain.types import Event, EventType, MemoryStatus
from app.persistence.views import WorldView, freeze


def _world() -> WorldView:
    return freeze(
        WorldView(
            campaign={"_id": "cmp_test", "current_turn": 3, "version": 2},
            player={
                "entity_id": "player_1",
                "version": 4,
                "character": {"hp": 20},
                "location": {"kind": "CELL", "ref_id": "cell_0_0", "slot": None},
            },
            current_cell={"cell_id": "cell_0_0", "version": 1},
            destination_cell={"cell_id": "cell_0_1", "version": 7},
            characters=(),
            items=(),
            container_items=(),
            config={},
            owned_items=(),
        )
    )


def test_deterministic_adapter_round_trips_canonical_resolution() -> None:
    event = Event(
        campaign_id="cmp_test",
        event_id="evt_4_0",
        turn_sequence=4,
        event_index=0,
        turn_id="turn_1",
        type=EventType.PLAYER_MOVED,
        actor_id="player_1",
        entity_ids=["player_1"],
        cell_id="cell_0_1",
        payload={"from_cell": "cell_0_0", "to_cell": "cell_0_1"},
        summary="Player moved north.",
        memory_status=MemoryStatus.NOT_REQUIRED,
    )
    original = Resolution(
        accepted=True,
        mutations=[
            DocumentMutation(
                "entities",
                "player_1",
                4,
                set_fields={"location.ref_id": "cell_0_1"},
                inc_fields={"player.new_cells_since_death": 1},
                add_to_set_fields={"player.discovered_cell_ids": "cell_0_1"},
            )
        ],
        inserts=[DocumentInsert("entities", {"entity_id": "item_split_4"})],
        events=[event],
        touched_entity_ids=["player_1"],
        touched_cell_ids=["cell_0_0", "cell_0_1"],
        expected_turn=3,
        expected_campaign_version=2,
        turn_id="turn_1",
        current_cell_id="cell_0_1",
        outcome_summary="Player moved north.",
    )

    bundle = resolution_to_mutation_bundle(original, bundle_id="fast:turn_1")
    rebuilt = apply_mutation_bundle(_world(), bundle, turn_id="turn_1")

    assert rebuilt.accepted
    assert rebuilt.mutations == original.mutations
    assert rebuilt.inserts == original.inserts
    assert rebuilt.events == original.events
    assert rebuilt.touched_entity_ids == original.touched_entity_ids
    assert rebuilt.touched_cell_ids == original.touched_cell_ids
    assert rebuilt.expected_turn == original.expected_turn
    assert rebuilt.expected_campaign_version == original.expected_campaign_version
    assert rebuilt.current_cell_id == original.current_cell_id


def test_model_bundle_cannot_use_application_owned_document_patch() -> None:
    bundle = MutationBundle(
        bundle_id="candidate_1",
        action_description="Rewrite the campaign",
        rationale="Attempted privilege escalation",
        draft_narration="Everything changes.",
        mutations=[
            ApplyDocumentMutation(
                collection="campaigns",
                document_id="cmp_test",
                expected_version=2,
                set_fields={"status": "WON"},
            )
        ],
    )

    valid, reason = validate_candidate_bundle(_world(), bundle)

    assert valid is False
    assert reason == "Mutation kind 'APPLY_DOCUMENT_MUTATION' is application-only"


def test_model_stat_path_compiles_to_canonical_character_path() -> None:
    from app.domain.mutations import MutateAttribute

    bundle = MutationBundle(
        bundle_id="candidate_1",
        action_description="Take damage",
        rationale="A resolved hit",
        draft_narration="You are struck.",
        mutations=[
            MutateAttribute(target_id="player_1", path="stats.hp", value=15)
        ],
    )

    result = apply_mutation_bundle(_world(), bundle)

    assert result.accepted
    assert result.mutations[0].set_fields == {"character.hp": 15}
