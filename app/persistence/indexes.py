"""Stable SQLite index catalog (TDD §9.2–§9.8, §12.5)."""

from __future__ import annotations

from dataclasses import dataclass

from app.persistence.sqlite import SQLiteDatabase

ASCENDING = 1
DESCENDING = -1


@dataclass(frozen=True)
class IndexSpec:
    collection: str
    name: str
    keys: tuple[tuple[str, int], ...]
    unique: bool = False


BTREE_INDEXES: tuple[IndexSpec, ...] = (
    IndexSpec(
        "campaigns",
        "campaign_status_updated",
        (("status", ASCENDING), ("updated_at", DESCENDING)),
    ),
    IndexSpec(
        "cells",
        "cell_coordinates_unique",
        (("campaign_id", ASCENDING), ("x", ASCENDING), ("y", ASCENDING)),
        True,
    ),
    IndexSpec(
        "cells",
        "cell_id_unique",
        (("campaign_id", ASCENDING), ("cell_id", ASCENDING)),
        True,
    ),
    IndexSpec("cells", "cell_generated", (("campaign_id", ASCENDING), ("generated", ASCENDING))),
    IndexSpec(
        "entities",
        "entity_id_unique",
        (("campaign_id", ASCENDING), ("entity_id", ASCENDING)),
        True,
    ),
    IndexSpec("entities", "entity_type", (("campaign_id", ASCENDING), ("entity_type", ASCENDING))),
    IndexSpec(
        "entities",
        "entity_location",
        (
            ("campaign_id", ASCENDING),
            ("location.ref_id", ASCENDING),
            ("location.kind", ASCENDING),
        ),
    ),
    IndexSpec(
        "entities",
        "character_status",
        (("campaign_id", ASCENDING), ("character.status", ASCENDING)),
    ),
    IndexSpec(
        "events",
        "event_sequence_unique",
        (
            ("campaign_id", ASCENDING),
            ("turn_sequence", ASCENDING),
            ("event_index", ASCENDING),
        ),
        True,
    ),
    IndexSpec(
        "events",
        "event_turn_unique",
        (("campaign_id", ASCENDING), ("turn_id", ASCENDING), ("event_index", ASCENDING)),
        True,
    ),
    IndexSpec(
        "events",
        "event_entities_recent",
        (
            ("campaign_id", ASCENDING),
            ("entity_ids", ASCENDING),
            ("turn_sequence", DESCENDING),
        ),
    ),
    IndexSpec(
        "events",
        "event_cell_recent",
        (("campaign_id", ASCENDING), ("cell_id", ASCENDING), ("turn_sequence", DESCENDING)),
    ),
    IndexSpec(
        "events",
        "event_memory_queue",
        (("memory_status", ASCENDING), ("created_at", ASCENDING)),
    ),
    IndexSpec(
        "memories",
        "memory_entities_recent",
        (
            ("campaign_id", ASCENDING),
            ("entity_ids", ASCENDING),
            ("created_turn", DESCENDING),
        ),
    ),
    IndexSpec(
        "memories",
        "memory_cell_recent",
        (("campaign_id", ASCENDING), ("cell_id", ASCENDING), ("created_turn", DESCENDING)),
    ),
    IndexSpec(
        "turns",
        "turn_id_unique",
        (("campaign_id", ASCENDING), ("turn_id", ASCENDING)),
        True,
    ),
    IndexSpec(
        "turns",
        "turn_sequence_recent",
        (("campaign_id", ASCENDING), ("turn_sequence", DESCENDING)),
    ),
    IndexSpec("turns", "turn_kind_created", (("kind", ASCENDING), ("created_at", DESCENDING))),
    IndexSpec("context_policies", "context_policy_version_unique", (("version", ASCENDING),), True),
    IndexSpec("context_policies", "context_policy_status", (("status", ASCENDING),)),
    IndexSpec(
        "evaluations",
        "evaluation_policy_created",
        (("policy_version", ASCENDING), ("created_at", DESCENDING)),
    ),
    IndexSpec(
        "quests",
        "quest_campaign_status",
        (("campaign_id", ASCENDING), ("status", ASCENDING)),
    ),
)

def create_btree_indexes(db: SQLiteDatabase) -> list[str]:
    """Return stable names; physical indexes are initialized with the schema."""
    return [spec.name for spec in BTREE_INDEXES]
