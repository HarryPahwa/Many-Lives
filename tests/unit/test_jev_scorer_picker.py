import pytest
from app.domain.mutations import MutationBundle
from app.domain.picker import select_winning_candidate
from app.domain.rng import TurnRng
from app.harness.jev_scorer import (
    CandidateScore,
    FakeJevScorer,
    JevScoringResult,
)


def test_jev_scoring_normalization():
    result = JevScoringResult(
        scores=[
            CandidateScore(bundle_id="c1", weight=30.0),
            CandidateScore(bundle_id="c2", weight=70.0),
        ]
    )
    weights = result.normalized_weights()
    assert pytest.approx(weights["c1"], 0.01) == 0.3
    assert pytest.approx(weights["c2"], 0.01) == 0.7


def test_jev_scoring_zero_fallback():
    result = JevScoringResult(
        scores=[
            CandidateScore(bundle_id="c1", weight=0.0),
            CandidateScore(bundle_id="c2", weight=0.0),
        ]
    )
    weights = result.normalized_weights()
    assert pytest.approx(weights["c1"], 0.01) == 0.5
    assert pytest.approx(weights["c2"], 0.01) == 0.5


def test_fake_jev_scorer():
    scorer = FakeJevScorer(predefined_weights={"c1": 10.0, "c2": 90.0})
    cand1 = MutationBundle(
        bundle_id="c1",
        action_description="a1",
        rationale="r1",
        draft_narration="d1",
        mutations=[],
    )
    cand2 = MutationBundle(
        bundle_id="c2",
        action_description="a2",
        rationale="r2",
        draft_narration="d2",
        mutations=[],
    )
    result = scorer.score_candidates(
        player_input="strike", candidates=[cand1, cand2], world_snapshot={}
    )
    weights = result.normalized_weights()
    assert pytest.approx(weights["c1"], 0.01) == 0.1
    assert pytest.approx(weights["c2"], 0.01) == 0.9


def test_deterministic_weighted_picker():
    cand1 = MutationBundle(
        bundle_id="c1",
        action_description="a1",
        rationale="r1",
        draft_narration="d1",
        mutations=[],
    )
    cand2 = MutationBundle(
        bundle_id="c2",
        action_description="a2",
        rationale="r2",
        draft_narration="d2",
        mutations=[],
    )

    rng1 = TurnRng(campaign_seed=42, turn_sequence=1)
    winner1 = select_winning_candidate(
        [cand1, cand2], {"c1": 0.99, "c2": 0.01}, rng1
    )
    assert winner1.bundle_id == "c1"

    rng2 = TurnRng(campaign_seed=42, turn_sequence=1)
    winner2 = select_winning_candidate(
        [cand1, cand2], {"c1": 0.01, "c2": 0.99}, rng2
    )
    assert winner2.bundle_id == "c2"


def test_single_candidate_picker():
    cand1 = MutationBundle(
        bundle_id="c1",
        action_description="a1",
        rationale="r1",
        draft_narration="d1",
        mutations=[],
    )
    rng = TurnRng(campaign_seed=123, turn_sequence=0)
    winner = select_winning_candidate([cand1], {"c1": 1.0}, rng)
    assert winner.bundle_id == "c1"
