"""Contract tests added by the harness slice."""

import pytest
from pydantic import ValidationError

from app.domain.types import (
    ActionClass,
    ClassRule,
    ContextManifest,
    ContextPolicy,
    MemoryReference,
    MemoryType,
    PolicyCreator,
    PolicyStatus,
    RetrievedMemory,
    RetrievalQuery,
    RetrievalResult,
    VectorMemoryConfig,
)


def test_harness_contracts_round_trip_through_json():
    policy = ContextPolicy(
        _id="context_policy_v1",
        version=1,
        status=PolicyStatus.ACTIVE,
        parent_version=None,
        created_by=PolicyCreator.HUMAN,
        rules={
            ActionClass.MOVE: ClassRule(
                mandatory=["player_state"],
                conditional=[],
                recent_event_window=0,
                vector_memory=VectorMemoryConfig(enabled=False),
            )
        },
        budget={"max_context_tokens": 3000},
        promotion_metrics=None,
        created_at="2026-09-26T00:00:00Z",
    )
    retrieval = RetrievalResult(
        memories=[
            RetrievedMemory(
                _id="mem_1",
                campaign_id="cmp_123",
                memory_type=MemoryType.DIALOGUE,
                entity_ids=["player_1", "npc_1"],
                cell_id="cell_1_1",
                source_event_ids=["evt_1_0"],
                created_turn=1,
                importance=0.5,
                text="Mara discussed the key.",
                embedding_model="example/embedding",
                created_at="2026-09-26T00:00:00Z",
                score=0.9,
            )
        ]
    )
    models = [
        policy,
        RetrievalQuery(
            campaign_id="cmp_123",
            query_text="ask Mara about the key",
            config=VectorMemoryConfig(enabled=True, top_k=3, entity_filter=True),
            entity_ids=["player_1", "npc_1"],
            cell_id="cell_1_1",
            recent_event_ids=["evt_1_0"],
        ),
        retrieval,
        ContextManifest(
            policy_version=1,
            action_class=ActionClass.MOVE,
            components=["current_cell"],
            entity_ids=["player_1"],
            event_ids=["evt_1_0"],
            memories=[MemoryReference(id="mem_1", score=0.9)],
            estimated_tokens=42,
            flags=[],
        ),
    ]

    for model in models:
        assert type(model).model_validate_json(model.model_dump_json()) == model


def test_retrieval_contracts_reject_unknown_fields():
    with pytest.raises(ValidationError):
        RetrievalResult.model_validate({"memories": [], "unexpected": True})
