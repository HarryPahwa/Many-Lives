from app.domain.mutations import AppendEvent, MutateAttribute, MutationBundle
from app.domain.types import EventType
from app.harness.candidate_generator import (
    CandidateGenerationResult,
    FakeCandidateGenerator,
    ModelCandidateGenerationResult,
    ModelMutationBundle,
    ModelCandidateGenerator,
    ModelAppendEvent,
    DialogueEventPayload,
    CombatEventPayload,
    load_candidate_count,
    load_candidate_max_output_tokens,
    load_candidate_pipeline_mode,
    load_candidate_validation_retries,
)
from app.domain.types import Role
from app.harness.model_client import FakeModelClient


def test_load_candidate_count_from_yaml():
    count = load_candidate_count("config/runtime_rules.yaml")
    assert count == 10
    assert load_candidate_max_output_tokens("config/runtime_rules.yaml") == 6000
    assert load_candidate_validation_retries("config/runtime_rules.yaml") == 1
    assert load_candidate_pipeline_mode("config/runtime_rules.yaml") == "one_step"


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


def test_model_candidate_generator_uses_structured_adjudicator_call():
    fixture = ModelCandidateGenerationResult(
        candidates=[
            ModelMutationBundle(
                bundle_id="generated_1",
                action_description="Inspect the rune",
                rationale="The rune is visible",
                draft_narration="You study the rune.",
                mutations=[],
            )
        ]
    )
    client = FakeModelClient(fixtures={(Role.ADJUDICATOR, "default"): fixture})
    generator = ModelCandidateGenerator(client)

    result = generator.generate_candidates(
        "inspect rune", {"current_cell": {"id": "cell_0_0"}}, candidate_count=1
    )

    assert result.candidates[0].bundle_id == fixture.candidates[0].bundle_id
    assert result.candidates[0].execution is None
    assert result.candidates[0].origin == "MODEL"
    assert generator.last_result is not None
    assert client.calls[0]["role"] == Role.ADJUDICATOR
    assert client.calls[0]["output_model"] is ModelCandidateGenerationResult
    assert '"candidate_count":1' in client.calls[0]["user"]


def test_model_dialogue_payload_survives_conversion_to_canonical_proposal():
    fixture = ModelCandidateGenerationResult(
        candidates=[
            ModelMutationBundle(
                bundle_id="talk_1",
                action_description="Open a conversation with the keeper",
                rationale="The keeper is present.",
                draft_narration="The keeper turns to hear you.",
                mutations=[
                    ModelAppendEvent(
                        event_type=EventType.DIALOGUE,
                        payload=DialogueEventPayload(
                            npc_id="keeper_1",
                            utterance=None,
                            dialogue_intent="INITIATE_CONVERSATION",
                            npc_reaction="ACKNOWLEDGE",
                        ),
                        summary="Player approaches the keeper to talk.",
                    )
                ],
            )
        ]
    )
    generator = ModelCandidateGenerator(
        FakeModelClient(fixtures={(Role.ADJUDICATOR, "default"): fixture})
    )

    result = generator.generate_candidates("talk to the keeper", {}, candidate_count=1)

    event = result.candidates[0].mutations[0]
    assert isinstance(event, AppendEvent)
    assert event.payload == {
        "npc_id": "keeper_1",
        "dialogue_intent": "INITIATE_CONVERSATION",
        "npc_reaction": "ACKNOWLEDGE",
    }


def test_combat_hp_delta_derives_event_damage_and_canonical_mutation():
    fixture = ModelCandidateGenerationResult(
        candidates=[
            ModelMutationBundle(
                bundle_id="hit_1",
                action_description="Strike the goblin",
                rationale="The attack connects.",
                draft_narration="Your weapon hits the goblin.",
                mutations=[
                    ModelAppendEvent(
                        event_type=EventType.ATTACK_RESOLVED,
                        payload=CombatEventPayload(
                            target_id="goblin_1", outcome="HIT", hp_delta=-6
                        ),
                        summary="Player hits the goblin for 6 damage.",
                    ),
                    # A malformed duplicate from the model must not override the
                    # typed combat payload.
                    MutateAttribute(
                        target_id="goblin_1",
                        path="stats.hp",
                        value="ADD",
                        op="ADD",
                    ),
                ],
            )
        ]
    )
    generator = ModelCandidateGenerator(
        FakeModelClient(fixtures={(Role.ADJUDICATOR, "default"): fixture})
    )

    result = generator.generate_candidates("attack goblin", {}, candidate_count=1)

    event, hp_change = result.candidates[0].mutations
    assert isinstance(event, AppendEvent)
    assert event.payload == {"target_id": "goblin_1", "outcome": "HIT", "damage": 6}
    assert isinstance(hp_change, MutateAttribute)
    assert hp_change.value == -6
    assert hp_change.op == "ADD"
