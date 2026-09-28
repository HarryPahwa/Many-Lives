"""Deterministic weighted candidate selection (TDD §13, §14).

Selects winning candidate mutation bundles from calibrated probability distributions
using seeded turn RNG.
"""

from typing import Sequence
from app.domain.mutations import MutationBundle
from app.domain.rng import TurnRng


def select_winning_candidate(
    candidates: Sequence[MutationBundle],
    normalized_weights: dict[str, float],
    rng: TurnRng,
    purpose: str = "check",
) -> MutationBundle:
    """Deterministically samples a winning candidate from normalized probability weights."""
    if not candidates:
        raise ValueError("Cannot select from empty candidates list")

    if len(candidates) == 1:
        return candidates[0]

    # Map candidates by bundle_id
    cand_map = {c.bundle_id: c for c in candidates}
    valid_ids = [c.bundle_id for c in candidates]

    # Build cumulative thresholds
    roll_val = rng.randint(purpose, 1, 10000) / 10000.0

    cumulative = 0.0
    for cid in valid_ids:
        weight = normalized_weights.get(cid, 0.0)
        cumulative += weight
        if roll_val <= cumulative or cumulative >= 0.9999:
            return cand_map[cid]

    # Fallback to last candidate on floating point rounding
    return candidates[-1]
