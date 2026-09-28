from app.domain.mutations import AppendEvent, MutateAttribute, MutationBundle
from app.domain.types import EventType
from app.harness.candidate_generator import (
    CandidateGenerationResult,
    FakeCandidateGenerator,
    load_candidate_count,
)


def test_load_candidate_count_from_yaml():
    count = load_candidate_count("config/runtime_rules.yaml")
    assert count == 3


def test_candidate_generation_with_condition_mutations():
    bundle = MutationBundle(
        bundle_id="cand_blind",
        action_description="Throw sand in goblin eyes",
        rationale="Sand causes blindness",
        draft_narration="You fling grit into the creature's eyes.",
        mutations=[
            MutateAttribute(
                target_id="goblin_1",
                path="physical_conditions",
                value="BLINDED",
                op="ADD",
            ),
            AppendEvent(
                event_type=EventType.ATTACK_RESOLVED,
                payload={"target_id": "goblin_1", "condition": "BLINDED"},
                summary="Goblin is blinded",
            ),
        ],
    )
    result = CandidateGenerationResult(candidates=[bundle])
    assert result.candidates[0].mutations[0].path == "physical_conditions"
    assert result.candidates[0].mutations[0].value == "BLINDED"
    assert result.candidates[0].mutations[0].op == "ADD"



def test_fake_candidate_generator_default():
    generator = FakeCandidateGenerator()
    world = {
        "player": {"id": "player_1"},
        "current_cell": {"id": "cell_0_0"},
    }
    result = generator.generate_candidates(
        player_input="kick chest", world_snapshot=world, candidate_count=3
    )
    assert len(result.candidates) == 3
    assert result.candidates[0].bundle_id == "cand_1"
    assert "kick chest" in result.candidates[0].action_description
    assert len(generator._calls) == 1


def test_fake_candidate_generator_predefined():
    predefined = CandidateGenerationResult(
        candidates=[
            MutationBundle(
                bundle_id="custom_1",
                action_description="Custom action",
                rationale="Special trigger",
                draft_narration="Custom narration.",
                mutations=[],
            )
        ]
    )
    generator = FakeCandidateGenerator(predefined_results=[predefined])
    result = generator.generate_candidates(
        player_input="inspect rune", world_snapshot={}, candidate_count=1
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].bundle_id == "custom_1"
