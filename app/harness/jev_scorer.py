"""Calibrated semantic scoring with Jev (TDD §8, §14).

Adjudicates surviving candidate mutation bundles against character capabilities,
environmental factors, and runtime balance rules to generate calibrated probability weights.
"""

from typing import Any, Mapping, Protocol
from pydantic import BaseModel, Field
import yaml

from app.domain.mutations import MutationBundle
from app.domain.types import DomainModel


class CandidateScore(DomainModel):
    bundle_id: str
    weight: float = Field(
        ge=0.0,
        description="Non-negative probability weight for this candidate outcome.",
    )
    rationale: str = Field(
        default="",
        description="Reasoning for this calibrated weight.",
    )


class JevScoringResult(DomainModel):
    scores: list[CandidateScore] = Field(
        min_length=1,
        description="Calibrated probability weights for all submitted candidate bundles.",
    )

    def normalized_weights(self) -> dict[str, float]:
        """Returns a mapping of bundle_id -> normalized probability (summing to 1.0)."""
        total = sum(s.weight for s in self.scores)
        if total <= 0:
            count = len(self.scores)
            return {s.bundle_id: 1.0 / count for s in self.scores}
        return {s.bundle_id: s.weight / total for s in self.scores}


JEV_SYSTEM_PROMPT = """You are Jev, the fast-path semantic adjudicator and probability calibrator.
Your role is to assign realistic, calibrated probability weights to candidate action outcomes based on:
1. The player's stats, active physical/mental conditions, and situational modifiers.
2. The targets' stats, entity_type (NPC vs ENEMY vs BOSS), active conditions, status, and room environment.
3. The runtime balance guidelines and DCs.

Entity Invariants:
- ENEMY / BOSS entities are strictly hostile. Dialogue attempts against hostile enemies are unlikely to produce favorable concessions and cannot yield friendly trust progression.
- Physical & mental conditions impact probability: e.g. a BLINDED or STUNNED enemy is much easier to strike or slip past; an ENRAGED enemy is aggressive and reckless.

For each candidate:
- Return a relative weight (0.0 to 100.0) reflecting its likelihood of occurring.
- 0.0 means virtually impossible given the context; higher weights mean higher relative likelihood.
- Return ONLY a valid JSON object matching the JevScoringResult schema.
"""


class JevScorer(Protocol):
    def score_candidates(
        self,
        player_input: str,
        candidates: list[MutationBundle],
        world_snapshot: Mapping[str, Any],
        runtime_rules: Mapping[str, Any] | None = None,
    ) -> JevScoringResult: ...


class FakeJevScorer:
    """Deterministic Jev scorer for unit tests and offline turns."""

    def __init__(self, predefined_weights: dict[str, float] | None = None):
        self._predefined_weights = predefined_weights or {}
        self._calls: list[dict[str, Any]] = []

    def score_candidates(
        self,
        player_input: str,
        candidates: list[MutationBundle],
        world_snapshot: Mapping[str, Any],
        runtime_rules: Mapping[str, Any] | None = None,
    ) -> JevScoringResult:
        self._calls.append(
            {
                "input": player_input,
                "candidates": candidates,
                "world": world_snapshot,
            }
        )
        if not candidates:
            return JevScoringResult(scores=[])

        scores: list[CandidateScore] = []
        for cand in candidates:
            weight = self._predefined_weights.get(cand.bundle_id, 1.0)
            scores.append(
                CandidateScore(
                    bundle_id=cand.bundle_id,
                    weight=weight,
                    rationale=f"Assigned weight {weight}",
                )
            )
        return JevScoringResult(scores=scores)
