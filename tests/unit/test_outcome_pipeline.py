from app.domain.mutation_validator import validate_candidate_bundle
from app.domain.mutations import AppendEvent, MutateAttribute, MutationBundle
from app.domain.types import ActionClass, EventType
from app.harness.mutation_compiler import (
    FakeMutationCompiler,
    validate_compilation_fidelity,
)
from app.harness.outcome_generator import (
    CandidateOutcome,
    CombatConsequence,
    DialogueConsequence,
    FakeOutcomeGenerator,
    mentioned_target_ids,
    validate_outcome,
)
from scripts.smoke_candidate_generator import mock_world


def test_explicit_visible_targets_are_resolved_without_a_model():
    _, world = mock_world()

    assert mentioned_target_ids("talk to the keeper", world) == ["keeper_1"]
    assert mentioned_target_ids("attack the tunnel goblin", world) == ["goblin_1"]


def test_social_outcome_must_preserve_target_and_spoken_response():
    _, world = mock_world()
    silent = CandidateOutcome(
        bundle_id="silent",
        action_description="The keeper stares.",
        rationale="The keeper is guarded.",
        actor_id="player_1",
        action_class=ActionClass.SOCIAL,
        target_ids=["keeper_1"],
        consequences=[
            DialogueConsequence(
                npc_id="keeper_1",
                utterance=None,
                dialogue_intent="INITIATE_CONVERSATION",
                npc_reaction="SILENCE",
            )
        ],
    )

    valid, reason = validate_outcome(
        silent,
        world,
        actor_id="player_1",
        action_class=ActionClass.SOCIAL,
        required_target_ids=["keeper_1"],
    )

    assert not valid
    assert reason == "OUTCOME_SPOKEN_RESPONSE_MISSING"


def test_counterattack_outcome_compiles_to_one_valid_multi_mutation_bundle():
    canonical, world = mock_world()
    outcome = CandidateOutcome(
        bundle_id="counter",
        action_description="Ada hits and the goblin counters.",
        rationale="Both combatants are alert.",
        actor_id="player_1",
        action_class=ActionClass.COMBAT,
        target_ids=["goblin_1"],
        consequences=[
            CombatConsequence(
                attacker_id="player_1",
                target_id="goblin_1",
                outcome="HIT",
                severity="MODERATE",
            ),
            CombatConsequence(
                attacker_id="goblin_1",
                target_id="player_1",
                outcome="HIT",
                severity="LIGHT",
            ),
        ],
    )
    compiler = FakeMutationCompiler()

    bundle = compiler.compile(outcome, world)

    assert len(bundle.mutations) == 4
    assert validate_compilation_fidelity(outcome, bundle) == (True, None)
    assert validate_candidate_bundle(canonical, bundle) == (True, None)


def test_fidelity_rejects_missing_damage_and_unrelated_target():
    outcome = CandidateOutcome(
        bundle_id="hit",
        action_description="Ada hits the goblin.",
        rationale="The strike connects.",
        actor_id="player_1",
        action_class=ActionClass.COMBAT,
        target_ids=["goblin_1"],
        consequences=[
            CombatConsequence(
                attacker_id="player_1",
                target_id="goblin_1",
                outcome="HIT",
                severity="LIGHT",
            )
        ],
    )
    missing_damage = MutationBundle(
        bundle_id="hit",
        action_description="Hit",
        rationale="Fixture",
        draft_narration="Hit.",
        mutations=[
            AppendEvent(
                event_type=EventType.ATTACK_RESOLVED,
                payload={"target_id": "goblin_1", "outcome": "HIT"},
                summary="Hit",
            )
        ],
    )
    unrelated = missing_damage.model_copy(
        update={
            "mutations": [
                *missing_damage.mutations,
                MutateAttribute(target_id="keeper_1", path="status", value="DEAD"),
            ]
        }
    )

    assert validate_compilation_fidelity(outcome, missing_damage) == (
        False,
        "COMPILED_DAMAGE_MISSING",
    )
    # Add required damage so the unrelated target becomes the dominant failure.
    unrelated.mutations.append(
        MutateAttribute(target_id="goblin_1", path="stats.hp", value=-2, op="ADD")
    )
    assert validate_compilation_fidelity(outcome, unrelated) == (
        False,
        "COMPILED_UNRELATED_TARGET",
    )


def test_outcome_and_compiler_fakes_record_calls():
    outcome = CandidateOutcome(
        bundle_id="wait",
        action_description="Wait cautiously.",
        rationale="Nothing presses the player.",
        actor_id="player_1",
        action_class=ActionClass.CREATIVE,
        target_ids=[],
        consequences=[],
    )
    generator = FakeOutcomeGenerator()
    result = generator.generate_outcomes(
        "wait",
        {},
        actor_id="player_1",
        action_class=ActionClass.CREATIVE,
        required_target_ids=[],
        candidate_count=2,
    )
    compiler = FakeMutationCompiler([MutationBundle(
        bundle_id="wait",
        action_description="Wait cautiously.",
        rationale="Fixture",
        draft_narration="You wait.",
        mutations=[],
    )])
    compiler.compile(outcome, {})

    assert len(result.candidates) == 2
    assert len(generator._calls) == 1
    assert len(compiler._calls) == 1
