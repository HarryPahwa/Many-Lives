"""Room-dressing model call (TDD §10.5); validation and persistence stay outside."""

from __future__ import annotations

import json

from app.domain.types import Role, RoomDressing, RoomPlan
from app.harness.model_client import ModelClient

_TIER_WORDS = ("quiet", "uneasy", "dangerous", "deadly", "lair")
_PROPERTY_TAGS = (
    "flammable",
    "breakable",
    "movable",
    "heavy",
    "container",
    "concealing",
    "light_source",
)
_STATE_KEYS = ("open_state", "lock_state", "condition", "orientation", "light_state")

_SYSTEM_PROMPT = """You dress a grounded dark-fantasy dungeon room. Return only the RoomDressing schema.
Use no modern objects. Room names are at most 40 characters and descriptions at most 200.
Use only slot IDs provided in the plan. Never invent stats, canonical IDs, locations, or quantities.
Your output is a proposal; deterministic code validates and persists it."""


def _tier_word(tier: int) -> str:
    return _TIER_WORDS[max(1, min(tier, len(_TIER_WORDS))) - 1]


def dress_room(plan: RoomPlan, *, client: ModelClient) -> RoomDressing:
    """Request one schema-valid dressing; do not validate or persist the proposal."""

    user_prompt = json.dumps(
        {
            "plan": plan.model_dump(mode="json"),
            "danger_tier": _tier_word(plan.tier),
            "allowed_property_tags": _PROPERTY_TAGS,
            "allowed_state_keys": _STATE_KEYS,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    result = client.structured(
        Role.DRESSER,
        _SYSTEM_PROMPT,
        user_prompt,
        RoomDressing,
        temperature=0.8,
        max_output_tokens=900,
        timeout_s=20.0,
    )
    return result.parsed
