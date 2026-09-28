"""Calibrated semantic scoring with Jev (TDD §8, §14).

Adjudicates surviving candidate mutation bundles against character capabilities,
environmental factors, and runtime balance rules to generate calibrated probability weights.
"""

import json
from collections.abc import Sequence
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from pydantic import BaseModel, Field
import yaml

from app.domain.mutations import MutationBundle
from app.domain.types import DomainModel
from app.config import Settings, get_settings


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


class JevScoringError(RuntimeError):
    """The Decisions API could not produce a safe candidate distribution."""


DecisionsTransport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_json(item) for item in value]
    return value


def _post_json(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout_s: float
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - configured API URL
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise JevScoringError(f"Decisions API returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise JevScoringError(f"Decisions API request failed: {exc}") from exc
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as exc:
        raise JevScoringError("Decisions API returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise JevScoringError("Decisions API response must be an object")
    return decoded


def load_runtime_rules(config_path: str = "config/runtime_rules.yaml") -> dict[str, Any]:
    path = Path(config_path)
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise JevScoringError(f"Unable to load runtime rules from {path}") from exc
    if not isinstance(loaded, dict):
        raise JevScoringError("Runtime rules must be a mapping")
    return loaded


class OpenRouterJevScorer:
    """Score candidates through OpenRouter's typed Decisions API."""

    QUESTION_ID = "candidate_outcome"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: DecisionsTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.transport = transport or _post_json
        self.last_usage: dict[str, Any] = {}
        self.last_latency_ms: int | None = None

    def score_candidates(
        self,
        player_input: str,
        candidates: list[MutationBundle],
        world_snapshot: Mapping[str, Any],
        runtime_rules: Mapping[str, Any] | None = None,
    ) -> JevScoringResult:
        if not candidates:
            raise JevScoringError("JEV requires at least one candidate")
        bundle_ids = [candidate.bundle_id for candidate in candidates]
        if len(set(bundle_ids)) != len(bundle_ids):
            raise JevScoringError("Candidate bundle IDs must be unique")
        if len(candidates) == 1:
            self.last_usage = {}
            self.last_latency_ms = None
            return JevScoringResult(
                scores=[
                    CandidateScore(
                        bundle_id=candidates[0].bundle_id,
                        weight=1.0,
                        rationale="Only one candidate survived deterministic validation",
                    )
                ]
            )
        if not self.settings.openrouter_api_key:
            raise JevScoringError("OPENROUTER_API_KEY is required for JEV")

        criteria = {
            candidate.bundle_id: json.dumps(
                {
                    "action": candidate.action_description,
                    "rationale": candidate.rationale,
                    "mutations": [
                        mutation.model_dump(mode="json") for mutation in candidate.mutations
                    ],
                },
                separators=(",", ":"),
            )
            for candidate in candidates
        }
        payload = {
            "model": self.settings.model_jev,
            "state": {
                "player_input": player_input,
                "world": _plain_json(world_snapshot),
                "runtime_rules": _plain_json(runtime_rules or {}),
            },
            "questions": {
                self.QUESTION_ID: {
                    "type": "choice",
                    "instructions": (
                        "Which candidate outcome is the most plausible result of the player's "
                        "action given the authoritative world state and runtime rules? Judge "
                        "semantic plausibility only; deterministic validation has already run."
                    ),
                    "criteria": criteria,
                }
            },
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
        }

        started = time.monotonic()
        response = self.transport(
            self.settings.jev_endpoint,
            headers,
            payload,
            self.settings.jev_timeout_s,
        )
        self.last_latency_ms = round((time.monotonic() - started) * 1000)
        self.last_usage = dict(response.get("usage") or {})

        answers = response.get("answers")
        answer = answers.get(self.QUESTION_ID) if isinstance(answers, dict) else None
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            raise JevScoringError("Decisions API did not return the requested choice answer")
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict):
            raise JevScoringError("JEV choice answer omitted probabilities")
        if set(probabilities) != set(bundle_ids):
            raise JevScoringError("JEV probabilities do not match submitted candidates")

        scores: list[CandidateScore] = []
        for bundle_id in bundle_ids:
            probability = probabilities[bundle_id]
            if isinstance(probability, bool) or not isinstance(probability, (int, float)):
                raise JevScoringError(f"Invalid probability for {bundle_id}")
            if not 0.0 <= float(probability) <= 1.0:
                raise JevScoringError(f"Probability for {bundle_id} is outside [0, 1]")
            scores.append(
                CandidateScore(
                    bundle_id=bundle_id,
                    weight=float(probability),
                    rationale=(
                        f"JEV choice probability; selected={answer.get('choice') == bundle_id}; "
                        f"confidence={answer.get('confidence', 'unknown')}"
                    ),
                )
            )
        return JevScoringResult(scores=scores)


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
