"""Unit tests for the multi-candidate Jev pipeline in turn orchestrator."""

from app.api.schemas import TurnRequest
from app.domain.mutations import MutateAttribute, MutationBundle
from app.harness.candidate_generator import CandidateGenerationResult, FakeCandidateGenerator
from app.harness.jev_scorer import CandidateScore, JevScoringResult, FakeJevScorer
from app.services.stubs import StubEngine, StubHarness
from app.services.turn_orchestrator import TurnOrchestrator


class MockHarnessWithJev(StubHarness):
    def __init__(self, generator: FakeCandidateGenerator, scorer: FakeJevScorer):
        super().__init__()
        self.candidate_generator = generator
        self.jev_scorer = scorer


def test_turn_orchestrator_runs_jev_pipeline():
    engine = StubEngine()
    camp = engine.create_campaign("Hero", seed=42)

    # In StubEngine, let's look up the actual character in the starting cell
    view = engine.load_world_view(camp.campaign_id, camp.player_id)
    # Add a mock enemy to starting cell for the test
    starting_cell = engine._require(camp.campaign_id).cells[view.visible_cell.cell_id]
    from app.services.stubs import _Character
    goblin = _Character(id="goblin_1", name="cave goblin", entity_type="ENEMY", hp=6, max_hp=6)
    starting_cell.characters.append(goblin)

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

    # Verify Jev scorer was called
    assert len(scorer._calls) == 1
    assert len(scorer._calls[0]["candidates"]) == 2
