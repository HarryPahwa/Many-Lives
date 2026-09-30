"""Unit tests for the multi-candidate Jev pipeline in turn orchestrator."""

from app.api.schemas import TurnRequest
from app.domain.mutations import MutateAttribute, MutationBundle
from app.harness.candidate_generator import CandidateGenerationResult, FakeCandidateGenerator
from app.harness.jev_scorer import CandidateScore, JevScoringResult, FakeJevScorer
from app.harness.mutation_compiler import FakeMutationCompiler
from app.harness.outcome_generator import (
    CandidateOutcome,
    CandidateOutcomeSet,
    CombatConsequence,
    FakeOutcomeGenerator,
)
from app.services.stubs import StubEngine, StubHarness
from app.services.turn_orchestrator import TurnOrchestrator


class MockHarnessWithJev(StubHarness):
    def __init__(self, generator: FakeCandidateGenerator, scorer: FakeJevScorer):
        super().__init__()
        self.candidate_generator = generator
        self.jev_scorer = scorer


def test_turn_orchestrator_runs_two_step_pipeline(monkeypatch):
    monkeypatch.setattr(
        "app.services.turn_orchestrator.load_candidate_pipeline_mode",
        lambda: "two_step",
    )
    engine = StubEngine()
    camp = engine.create_campaign("Hero", seed=42)
    state = engine._require(camp.campaign_id)
    from app.services.stubs import _Character

    state.cells[state.player_cell].characters.append(
        _Character(id="goblin_1", name="tunnel goblin", entity_type="ENEMY", hp=8, max_hp=8)
    )
    outcome = CandidateOutcome(
        bundle_id="hit",
        action_description="Hit the tunnel goblin.",
        rationale="The goblin is in reach.",
        actor_id=camp.player_id,
        action_class="COMBAT",
        target_ids=["goblin_1"],
        consequences=[
            CombatConsequence(
                attacker_id=camp.player_id,
                target_id="goblin_1",
                outcome="HIT",
                severity="LIGHT",
            )
        ],
    )
    harness = StubHarness()
    harness.outcome_generator = FakeOutcomeGenerator(
        [CandidateOutcomeSet(candidates=[outcome])]
    )
    harness.mutation_compiler = FakeMutationCompiler()
    harness.jev_scorer = FakeJevScorer({"hit": 1.0})

    result = TurnOrchestrator(engine=engine, harness=harness).take_turn(
        camp.campaign_id,
        TurnRequest(
            player_id=camp.player_id,
            turn_id="two-step-hit",
            input="attack the tunnel goblin creatively",
        ),
    )

    assert result.accepted
    assert len(harness.outcome_generator._calls) == 1
    assert len(harness.mutation_compiler._calls) == 1
    assert state.cells[state.player_cell].characters[0].hp == 6


def test_turn_orchestrator_runs_jev_pipeline():
    engine = StubEngine()
    camp = engine.create_campaign("Hero", seed=42)

    # In StubEngine, let's look up the actual character in the starting cell
    view = engine.load_world_view(camp.campaign_id, camp.player_id)
    # Add a mock enemy to starting cell for the test
    starting_cell = engine._require(camp.campaign_id).cells[view.visible_cell.cell_id]
    from app.services.stubs import _Character, _Item
    goblin = _Character(id="goblin_1", name="cave goblin", entity_type="ENEMY", hp=6, max_hp=6)
    starting_cell.characters.append(goblin)
    engine._require(camp.campaign_id).inventory.append(
        _Item(id="axe_1", name="woodsman's axe", where="equipped", slot="WEAPON")
    )

    # Prepare candidate generator and scorer
    candidates = [
        MutationBundle(
            bundle_id="cand_1",
            action_description="Strike the goblin",
            rationale="Normal melee attack",
            draft_narration="You swing your blade cleanly.",
            mutations=[
                MutateAttribute(
                    kind="MUTATE_ATTRIBUTE",
                    target_id="goblin_1",
                    path="stats.hp",
                    value=4,
                    op="SET",
                )
            ],
        ),
        MutationBundle(
            bundle_id="cand_2",
            action_description="Kick dirt in goblin's eyes",
            rationale="Tactical distraction causing blindness",
            draft_narration="You kick up a cloud of loose cavern dirt into the goblin's eyes.",
            mutations=[
                MutateAttribute(
                    kind="MUTATE_ATTRIBUTE",
                    target_id="goblin_1",
                    path="physical_conditions",
                    value="BLINDED",
                    op="ADD",
                )
            ],
        ),
    ]

    generator = FakeCandidateGenerator([CandidateGenerationResult(candidates=candidates)])
    scorer = FakeJevScorer({"cand_1": 1.0, "cand_2": 9.0})
    harness = MockHarnessWithJev(generator, scorer)

    orchestrator = TurnOrchestrator(engine=engine, harness=harness)

    # Execute freeform action
    result = orchestrator.take_turn(
        camp.campaign_id,
        TurnRequest(
            player_id=camp.player_id,
            turn_id="turn_0001",
            input="kick dirt into the goblin's eyes",
        ),
    )

    assert result.accepted is True
    assert result.turn_sequence == 1
    assert result.status == "COMMITTED" or result.status == "NARRATED"

    # Verify candidate generator was called
    assert len(generator._calls) == 1
    assert generator._calls[0]["input"] == "kick dirt into the goblin's eyes"
    generated_world = generator._calls[0]["world"]
    assert generated_world["player"]["character"]["attack"] == 5
    assert generated_world["player_inventory"][0]["name"] == "woodsman's axe"
    assert generated_world["player_inventory"][0]["item"]["subtype"] == "WEAPON"
    generated_goblin = next(
        character for character in generated_world["characters"]
        if character["entity_id"] == "goblin_1"
    )
    assert generated_goblin["character"]["hp"] == 6

    # Verify Jev scorer was called
    assert len(scorer._calls) == 1
    assert len(scorer._calls[0]["candidates"]) == 2
    assert scorer._calls[0]["world"] == generated_world


def test_ungrounded_fast_interpretation_falls_through_to_candidates():
    from app.services.stubs import _Character, _Item

    engine = StubEngine()
    camp = engine.create_campaign("Hero", seed=42)
    state = engine._require(camp.campaign_id)
    state.inventory.append(
        _Item(id="axe_1", name="woodsman's axe", where="equipped", slot="WEAPON")
    )
    state.cells[state.player_cell].characters.append(
        _Character(
            id="goblin_1", name="tunnel goblin", entity_type="ENEMY", hp=6, max_hp=6
        )
    )
    candidates = [
        MutationBundle(
            bundle_id="hit",
            action_description="Hit the goblin with the equipped weapon",
            rationale="The axe is equipped and the goblin is in the room",
            draft_narration="The axe bites into the goblin.",
            mutations=[
                MutateAttribute(
                    target_id="goblin_1", path="stats.hp", value=3, op="SET"
                )
            ],
        ),
        MutationBundle(
            bundle_id="miss",
            action_description="Swing the equipped weapon and miss",
            rationale="The goblin may evade the swing",
            draft_narration="The axe whistles past the goblin.",
            mutations=[],
        ),
    ]
    generator = FakeCandidateGenerator(
        [CandidateGenerationResult(candidates=candidates)]
    )
    scorer = FakeJevScorer({"hit": 1.0, "miss": 0.0})
    orchestrator = TurnOrchestrator(
        engine=engine, harness=MockHarnessWithJev(generator, scorer)
    )

    result = orchestrator.take_turn(
        camp.campaign_id,
        TurnRequest(
            player_id=camp.player_id,
            turn_id="use-weapon",
            input="use weapon on goblin",
        ),
    )

    assert result.accepted
    assert generator._calls[0]["input"] == "use weapon on goblin"
    assert generator._calls[0]["count"] == 10
    assert scorer._calls[0]["world"] == generator._calls[0]["world"]


def test_all_rejected_candidates_regenerate_once_with_validation_feedback():
    engine = StubEngine()
    camp = engine.create_campaign("Hero", seed=42)
    invalid = MutationBundle(
        bundle_id="bad",
        action_description="Malformed damage",
        rationale="Bad model value",
        draft_narration="You attempt a strike.",
        mutations=[
            MutateAttribute(
                target_id=camp.player_id,
                path="stats.hp",
                value="ADD",
                op="ADD",
            )
        ],
    )
    corrected = MutationBundle(
        bundle_id="corrected",
        action_description="Wait cautiously",
        rationale="No state change is required",
        draft_narration="You wait and watch.",
        mutations=[],
    )
    generator = FakeCandidateGenerator(
        [
            CandidateGenerationResult(candidates=[invalid]),
            CandidateGenerationResult(candidates=[corrected]),
        ]
    )
    scorer = FakeJevScorer({"corrected": 1.0})
    orchestrator = TurnOrchestrator(
        engine=engine, harness=MockHarnessWithJev(generator, scorer)
    )

    result = orchestrator.take_turn(
        camp.campaign_id,
        TurnRequest(
            player_id=camp.player_id,
            turn_id="retry-all-rejected",
            input="perform an inscrutable maneuver",
        ),
    )

    assert result.accepted is True
    assert len(generator._calls) == 2
    assert generator._calls[0]["validation_feedback"] is None
    assert generator._calls[1]["validation_feedback"] == [
        "Invalid numeric value for 'stats.hp': 'ADD'"
    ]
    assert [c.bundle_id for c in scorer._calls[0]["candidates"]] == ["corrected"]
