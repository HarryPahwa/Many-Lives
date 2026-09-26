"""Idempotent MongoDB index creation (TDD §9.2–§9.8, §12.5)."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database
from pymongo.operations import SearchIndexModel


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

MEMORY_VECTOR_INDEX_NAME = "memories_vector"
MEMORY_VECTOR_FILTER_PATHS = ("campaign_id", "entity_ids", "cell_id", "memory_type")


def create_btree_indexes(db: Database) -> list[str]:
    """Create all ordinary indexes and return their stable names."""
    created: list[str] = []
    for spec in BTREE_INDEXES:
        name = db[spec.collection].create_index(
            list(spec.keys), name=spec.name, unique=spec.unique
        )
        created.append(name)
    return created


def memory_vector_definition(dimensions: int) -> dict[str, list[dict[str, Any]]]:
    """Build the Atlas Vector Search definition after validating its dimension."""
    if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions <= 0:
        raise ValueError("Embedding dimensions must be a positive integer")
    return {
        "fields": [
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": dimensions,
                "similarity": "cosine",
            },
            *({"type": "filter", "path": path} for path in MEMORY_VECTOR_FILTER_PATHS),
        ]
    }


def _find_vector_index(collection: Any) -> dict[str, Any] | None:
    indexes = collection.list_search_indexes(name=MEMORY_VECTOR_INDEX_NAME)
    return next(iter(indexes), None)


def ensure_memory_vector_index(
    db: Database,
    dimensions: int,
    *,
    timeout_s: float = 60.0,
    poll_interval_s: float = 1.0,
) -> None:
    """Create the memory vector index if absent and wait until it is queryable."""
    if timeout_s < 0 or poll_interval_s < 0:
        raise ValueError("Vector index timeout values cannot be negative")

    definition = memory_vector_definition(dimensions)
    collection = db.memories
    current = _find_vector_index(collection)
    if current is None:
        collection.create_search_index(
            model=SearchIndexModel(
                definition=definition,
                name=MEMORY_VECTOR_INDEX_NAME,
                type="vectorSearch",
            )
        )
    elif current.get("latestDefinition") not in (None, definition):
        raise RuntimeError(
            "Existing memories_vector definition does not match EMBEDDING_DIMS"
        )

    deadline = time.monotonic() + timeout_s
    while True:
        current = _find_vector_index(collection)
        if current is not None and current.get("queryable") is True:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out waiting for memories_vector to become queryable")
        time.sleep(poll_interval_s)
