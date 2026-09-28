import pytest
from types import MappingProxyType
from app.config import Settings
from app.domain.mutations import MutationBundle
from app.domain.picker import select_winning_candidate
from app.domain.rng import TurnRng
from app.harness.jev_scorer import (
    CandidateScore,
    FakeJevScorer,
    JevScoringResult,
    JevScoringError,
    OpenRouterJevScorer,
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


def test_openrouter_jev_uses_decisions_api_choice_probabilities():
    captured = {}

    def transport(url, headers, payload, timeout_s):
        captured.update(
            url=url, headers=headers, payload=payload, timeout_s=timeout_s
        )
        return {
            "answers": {
                "candidate_outcome": {
                    "type": "choice",
                    "choice": "c2",
                    "probabilities": {"c1": 0.2, "c2": 0.8},
                    "confidence": 0.7,
                }
            },
            "usage": {"input_tokens": 123, "output_tokens": 8, "cost": 0.00001},
        }

    settings = Settings(
        openrouter_api_key="test-key",
        model_jev="typesafe/jev-1.13",
        jev_endpoint="https://openrouter.ai/api/alpha/decisions",
        jev_timeout_s=7,
    )
    scorer = OpenRouterJevScorer(settings, transport=transport)
    candidates = [
        MutationBundle(
            bundle_id=bundle_id,
            action_description=f"Outcome {bundle_id}",
            rationale="plausible",
            draft_narration="Something happens.",
            mutations=[],
        )
        for bundle_id in ("c1", "c2")
    ]

    result = scorer.score_candidates(
        "kick dirt",
        candidates,
        MappingProxyType({"player": MappingProxyType({"id": "player_1"})}),
        {"checks": {}},
    )

    assert captured["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["payload"]["model"] == "typesafe/jev-1.13"
    question = captured["payload"]["questions"]["candidate_outcome"]
    assert question["type"] == "choice"
    assert set(question["criteria"]) == {"c1", "c2"}
    assert captured["payload"]["state"]["world"]["player"] == {"id": "player_1"}
    assert result.normalized_weights() == {"c1": 0.2, "c2": 0.8}
    assert scorer.last_usage["input_tokens"] == 123


def test_openrouter_jev_rejects_mismatched_decision_probabilities():
    def transport(_url, _headers, _payload, _timeout_s):
        return {
            "answers": {
                "candidate_outcome": {
                    "type": "choice",
                    "choice": "c1",
                    "probabilities": {"c1": 1.0},
                }
            }
        }

    scorer = OpenRouterJevScorer(
        Settings(openrouter_api_key="test-key"), transport=transport
    )
    candidates = [
        MutationBundle(
            bundle_id=bundle_id,
            action_description=bundle_id,
            rationale="test",
            draft_narration="test",
            mutations=[],
        )
        for bundle_id in ("c1", "c2")
    ]

    with pytest.raises(JevScoringError, match="do not match"):
        scorer.score_candidates("act", candidates, {})


def test_openrouter_jev_skips_network_for_one_surviving_candidate():
    def unexpected_transport(*_args):
        raise AssertionError("single candidate should not call JEV")

    scorer = OpenRouterJevScorer(Settings(), transport=unexpected_transport)
    candidate = MutationBundle(
        bundle_id="only",
        action_description="Only valid outcome",
        rationale="All others were filtered",
        draft_narration="Only one thing can happen.",
        mutations=[],
    )

    result = scorer.score_candidates("act", [candidate], {})

    assert result.normalized_weights() == {"only": 1.0}
