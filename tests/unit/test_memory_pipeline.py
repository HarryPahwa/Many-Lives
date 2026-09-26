import mongomock

from app.domain.types import Event, EventType, MemoryStatus, VectorMemoryConfig
from app.harness.memory_pipeline import (
    MEMORY_RULES,
    decode_embedding,
    extract_memories,
    memory_status_for,
    memory_text,
)
from app.harness.memory_retriever import retrieve
from app.harness.model_client import FakeModelClient


def event(event_id: str = "evt_1_0", event_type: EventType = EventType.DIALOGUE) -> Event:
    return Event(
        campaign_id="cmp_1",
        event_id=event_id,
        turn_sequence=1,
        event_index=0,
        turn_id="turn_1",
        type=event_type,
        actor_id="player_1",
        entity_ids=["player_1", "npc_1"],
        cell_id="cell_1_1",
        summary="Player spoke with Mara.",
    )


def test_memory_rules_cover_every_event_type():
    assert set(MEMORY_RULES) == set(EventType)
    assert memory_status_for(event()) is MemoryStatus.PENDING
    assert memory_status_for(event(event_type=EventType.PLAYER_MOVED)) is MemoryStatus.NOT_REQUIRED


def test_memory_text_is_stable_and_groups_related_events():
    text = memory_text([event(), event("evt_1_1", EventType.DISPOSITION_CHANGED)], {"npc_1": "Mara"})
    assert "turn 1" in text
    assert "related events followed" in text


def test_extract_memories_stores_float32_and_marks_sources_complete():
    db = mongomock.MongoClient().dungeon_test
    source = event().model_dump(mode="json")
    db.events.insert_one(source)
    ids = extract_memories([source["event_id"]], db=db, client=FakeModelClient(embedding_dims=4))

    stored = db.memories.find_one({"_id": ids[0]})
    assert len(decode_embedding(stored["embedding"])) == 4
    assert db.events.find_one({"event_id": source["event_id"]})["memory_status"] == "COMPLETE"


def test_vector_failure_is_non_fatal_and_flagged():
    result = retrieve(
        db=mongomock.MongoClient().dungeon_test,
        client=FakeModelClient(embedding_dims=4),
        campaign_id="cmp_1",
        query_text="ask Mara",
        cfg=VectorMemoryConfig(enabled=True, top_k=3),
        entity_ids=["npc_1"],
        cell_id="cell_1_1",
        recent_event_ids=set(),
    )
    assert result.memories == []
    assert result.flags == ["VECTOR_UNAVAILABLE"]
