"""Opt-in live smoke test for OpenRouter's JEV Decisions API.

Run only when a real ``OPENROUTER_API_KEY`` is intentionally configured:

    uv run python scripts/smoke_jev.py
"""

from __future__ import annotations

import json

from app.config import get_settings
from app.domain.mutations import MutationBundle
from app.harness.jev_scorer import OpenRouterJevScorer


def main() -> None:
    settings = get_settings()
    if not settings.openrouter_api_key:
        raise SystemExit("OPENROUTER_API_KEY is not configured; no request was sent")

    candidates = [
        MutationBundle(
            bundle_id="distract",
            action_description="The goblin is briefly distracted by thrown dirt",
            rationale="Loose dirt is present and the goblin can see the player",
            draft_narration="The goblin recoils from the grit.",
            mutations=[],
        ),
        MutationBundle(
            bundle_id="ignore",
            action_description="The goblin ignores the thrown dirt",
            rationale="The attempt may be clumsy or obvious",
            draft_narration="The goblin does not flinch.",
            mutations=[],
        ),
    ]
    scorer = OpenRouterJevScorer(settings)
    result = scorer.score_candidates(
        "I kick loose dirt toward the goblin's eyes",
        candidates,
        {
            "player": {"skill": 3, "physical_conditions": []},
            "current_cell": {"description": "A dry chamber with loose dirt"},
            "characters": [
                {
                    "id": "goblin_1",
                    "entity_type": "ENEMY",
                    "physical_conditions": [],
                }
            ],
        },
    )
    print(
        json.dumps(
            {
                "model": settings.model_jev,
                "weights": result.normalized_weights(),
                "usage": scorer.last_usage,
                "latency_ms": scorer.last_latency_ms,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
