"""Campaign-scoped Atlas Vector Search retrieval."""

from __future__ import annotations

from app.domain.types import RetrievalResult, RetrievedMemory, VectorMemoryConfig
from app.harness.model_client import ModelClient


def retrieve(
    *,
    db,
    client: ModelClient,
    campaign_id: str,
    query_text: str,
    cfg: VectorMemoryConfig,
    entity_ids: list[str] | None,
    cell_id: str | None,
    recent_event_ids: set[str],
) -> RetrievalResult:
    """Never let an unavailable vector index fail a player turn."""

    if not cfg.enabled or cfg.top_k == 0:
        return RetrievalResult()
    try:
        query_vector = client.embed([query_text])[0]
        filters: list[dict[str, object]] = [{"campaign_id": campaign_id}]
        if cfg.entity_filter and entity_ids:
            filters.append({"entity_ids": {"$in": entity_ids}})
        if cfg.cell_filter and cell_id:
            filters.append({"cell_id": cell_id})
        if cfg.memory_types:
            filters.append({"memory_type": {"$in": [item.value for item in cfg.memory_types]}})
        pipeline = [
            {
                "$vectorSearch": {
                    "index": "memories_vector",
                    "path": "embedding",
                    "queryVector": query_vector,
                    "numCandidates": 20 * cfg.top_k,
                    "limit": cfg.top_k,
                    "filter": {"$and": filters},
                }
            },
            {
                "$project": {
                    "_id": 1,
                    "campaign_id": 1,
                    "schema_version": 1,
                    "text": 1,
                    "memory_type": 1,
                    "importance": 1,
                    "entity_ids": 1,
                    "cell_id": 1,
                    "created_turn": 1,
                    "source_event_ids": 1,
                    "embedding_model": 1,
                    "created_at": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]
        documents = list(db.memories.aggregate(pipeline))
        memories = [
            RetrievedMemory.model_validate(document)
            for document in documents
            if not set(document.get("source_event_ids", [])).issubset(recent_event_ids)
        ]
        return RetrievalResult(memories=memories)
    except Exception:
        return RetrievalResult(flags=["VECTOR_UNAVAILABLE"])
