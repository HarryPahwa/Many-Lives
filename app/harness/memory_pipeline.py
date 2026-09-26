"""Post-commit, campaign-scoped semantic memory extraction."""

from __future__ import annotations

import struct
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from bson import Binary

from app.domain.types import Event, EventType, MemoryStatus, MemoryType
from app.harness.model_client import ModelClient


@dataclass(frozen=True)
class MemoryRule:
    memory_type: MemoryType
    importance: float


MEMORY_RULES: dict[EventType, MemoryRule | None] = {event_type: None for event_type in EventType}
MEMORY_RULES.update(
    {
        EventType.DISPOSITION_CHANGED: MemoryRule(MemoryType.RELATIONSHIP, 0.9),
        EventType.ENTITY_DIED: MemoryRule(MemoryType.COMBAT, 0.6),
        EventType.ATTACK_RESOLVED: MemoryRule(MemoryType.COMBAT, 0.8),
        EventType.ITEM_TRANSFERRED: MemoryRule(MemoryType.ITEM, 0.8),
        EventType.DIALOGUE: MemoryRule(MemoryType.DIALOGUE, 0.5),
        EventType.FACT_REVEALED: MemoryRule(MemoryType.DISCOVERY, 0.8),
        EventType.FEATURE_STATE_CHANGED: MemoryRule(MemoryType.ENVIRONMENT, 0.4),
        EventType.FEATURE_CREATED: MemoryRule(MemoryType.ENVIRONMENT, 0.4),
        EventType.QUEST_ISSUED: MemoryRule(MemoryType.QUEST, 0.9),
        EventType.QUEST_STATE_CHANGED: MemoryRule(MemoryType.QUEST, 0.9),
        EventType.BOSS_DOOR_UNLOCKED: MemoryRule(MemoryType.BOSS, 0.9),
        EventType.PLAYER_DIED: MemoryRule(MemoryType.COMBAT, 0.6),
    }
)


def memory_status_for(event: Event) -> MemoryStatus:
    """The deterministic commit-time memory status for a canonical event."""

    return MemoryStatus.PENDING if MEMORY_RULES[event.type] else MemoryStatus.NOT_REQUIRED


def memory_text(events: list[Event], names: dict[str, str]) -> str:
    """A stable, one-sentence past-tense description of related turn events."""

    event = events[0]
    actors = [names.get(entity_id, entity_id) for entity_id in event.entity_ids]
    subject = ", ".join(actors) if actors else "the player"
    detail = event.summary.rstrip(".") or event.type.value.replace("_", " ").lower()
    if len(events) > 1:
        detail = f"{detail}; related events followed"
    return f"On turn {event.turn_sequence} in {event.cell_id}, {subject} {detail.lower()}."


def _binary(vector: list[float]) -> Binary:
    return Binary(struct.pack(f"<{len(vector)}f", *vector), subtype=0)


def decode_embedding(value: Binary) -> list[float]:
    return list(struct.unpack(f"<{len(value) // 4}f", bytes(value)))


def _as_event(document: dict[str, Any]) -> Event:
    document = dict(document)
    document.pop("_id", None)
    return Event.model_validate(document)


def extract_memories(campaign_id: str, event_ids: list[str], *, db, client: ModelClient) -> list[str]:
    """Embed and store memories; failures are recorded on source events."""

    documents = list(
        db.events.find({"campaign_id": campaign_id, "event_id": {"$in": event_ids}})
    )
    events = [_as_event(document) for document in documents]
    grouped: dict[tuple[str, int], list[Event]] = defaultdict(list)
    for event in events:
        if MEMORY_RULES[event.type]:
            grouped[(event.campaign_id, event.turn_sequence)].append(event)
    if not grouped:
        return []

    batches = list(grouped.values())
    texts = [memory_text(group, {}) for group in batches]
    try:
        vectors = [
            vector
            for start in range(0, len(texts), 64)
            for vector in client.embed(texts[start : start + 64])
        ]
        if len(vectors) != len(batches):
            raise ValueError("Embedding count did not match memory count")
        ids: list[str] = []
        for group, text, vector in zip(batches, texts, vectors, strict=True):
            rule = MEMORY_RULES[group[0].type]
            assert rule is not None
            memory_id = f"mem_{group[0].campaign_id}_{group[0].turn_sequence}_{group[0].event_index}"
            db.memories.replace_one(
                {"_id": memory_id, "campaign_id": group[0].campaign_id},
                {
                    "_id": memory_id,
                    "campaign_id": group[0].campaign_id,
                    "text": text,
                    "memory_type": rule.memory_type.value,
                    "importance": rule.importance,
                    "entity_ids": sorted({entity_id for event in group for entity_id in event.entity_ids}),
                    "cell_id": group[0].cell_id,
                    "created_turn": group[0].turn_sequence,
                    "source_event_ids": [event.event_id for event in group],
                    "embedding": _binary(vector),
                    "embedding_model": getattr(getattr(client, "settings", None), "embedding_model", "fake"),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "schema_version": 1,
                },
                upsert=True,
            )
            db.events.update_many(
                {"campaign_id": group[0].campaign_id, "event_id": {"$in": [event.event_id for event in group]}},
                {"$set": {"memory_status": MemoryStatus.COMPLETE.value}},
            )
            ids.append(memory_id)
        return ids
    except Exception:
        db.events.update_many(
            {"campaign_id": campaign_id, "event_id": {"$in": event_ids}},
            {"$set": {"memory_status": MemoryStatus.FAILED.value}, "$inc": {"memory_attempts": 1}},
        )
        raise


class MemoryWorker:
    """Small in-process queue; callers opt in only with canonical Mongo events."""

    def __init__(self, db, client: ModelClient) -> None:
        self.db = db
        self.client = client
        self._pending: deque[tuple[str, list[str]]] = deque()
        self._lock = Lock()

    def enqueue(self, campaign_id: str, event_ids: list[str]) -> None:
        if event_ids:
            with self._lock:
                self._pending.append((campaign_id, list(event_ids)))

    def drain(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    return
                campaign_id, event_ids = self._pending.popleft()
            try:
                extract_memories(campaign_id, event_ids, db=self.db, client=self.client)
            except Exception:
                continue

    def sweep(self) -> None:
        documents = self.db.events.find(
            {
                "$or": [
                    {"memory_status": MemoryStatus.PENDING.value},
                    {"memory_status": MemoryStatus.FAILED.value, "memory_attempts": {"$lt": 3}},
                ]
            },
            {"campaign_id": 1, "event_id": 1},
        )
        grouped: dict[str, list[str]] = defaultdict(list)
        for document in documents:
            grouped[document["campaign_id"]].append(document["event_id"])
        for campaign_id, event_ids in grouped.items():
            self.enqueue(campaign_id, event_ids)
        self.drain()
