"""Campaign-scoped in-process vector retrieval over SQLite documents."""

from __future__ import annotations

from app.domain.types import RetrievalResult, RetrievedMemory, VectorMemoryConfig
from app.harness.model_client import ModelClient
from app.harness.memory_pipeline import decode_embedding


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


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
        filters: dict[str, object] = {"campaign_id": campaign_id}
        if cfg.entity_filter and entity_ids:
            filters["entity_ids"] = {"$in": entity_ids}
        if cfg.cell_filter and cell_id:
            filters["cell_id"] = cell_id
        if cfg.memory_types:
            filters["memory_type"] = {"$in": [item.value for item in cfg.memory_types]}
        documents = list(db.memories.find(filters))
        for document in documents:
            document["score"] = _cosine(query_vector, decode_embedding(document["embedding"]))
        documents.sort(key=lambda item: item["score"], reverse=True)
        documents = documents[: cfg.top_k]
        memories = [
            RetrievedMemory.model_validate(document)
            for document in documents
            if not set(document.get("source_event_ids", [])).issubset(recent_event_ids)
        ]
        return RetrievalResult(memories=memories)
    except Exception:
        return RetrievalResult(flags=["VECTOR_UNAVAILABLE"])
