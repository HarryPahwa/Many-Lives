"""Tests for bounded, read-only context construction."""

from collections.abc import Mapping, Sequence
from typing import Any

from app.domain.types import (
    ActionClass,
    Event,
    EventType,
    MemoryStatus,
    RetrievalQuery,
    RetrievalResult,
    RetrievedMemory,
    Role,
)
from app.harness.context_builder import build_context
from app.harness.context_policy import seed_context_policy


class FakeView:
    def __init__(self, state: Mapping[str, Any], events: Sequence[Event]) -> None:
        self.state = state
        self.events = events

    def current_state(self, **_: Any) -> Mapping[str, Any]:
        return self.state

    def recent_events(self, *, limit: int, **_: Any) -> Sequence[Event]:
        return self.events[-limit:]


def event(event_id: str, turn: int) -> Event:
    return Event(
        type=EventType.FEATURE_STATE_CHANGED,
        actor_id="player_1",
        entity_ids=["player_1"],
        cell_id="cell_1_1",
        payload={},
        summary=f"event {event_id}",
        event_id=event_id,
        campaign_id="campaign_1",
        turn_sequence=turn,
        event_index=0,
        turn_id=f"turn_{turn}",
        memory_status=MemoryStatus.COMPLETE,
    )


def memory(memory_id: str, score: float, text: str = "old detail") -> RetrievedMemory:
    return RetrievedMemory(
        _id=memory_id,
        campaign_id="campaign_1",
        memory_type="ENVIRONMENT",
        entity_ids=["player_1"],
        cell_id="cell_1_1",
        source_event_ids=["evt_old"],
        created_turn=1,
        importance=0.5,
        text=text,
        embedding_model="test/embedding",
        created_at="2026-09-26T00:00:00Z",
        score=score,
    )


def inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        {"campaign_id": "campaign_1"},
        {"entity_id": "player_1"},
        {"cell_id": "cell_1_1"},
    )


def test_context_has_fixed_component_order_and_campaign_scoped_retrieval():
    campaign, player, room = inputs()
    captured: list[RetrievalQuery] = []
    view = FakeView(
        {
            "player_state": {"hp": 8},
            "player_inventory": [{"name": "brass key"}],
            "current_cell": {"name": "Moss Crypt"},
            "visible_entities": [{"entity_id": "npc_mara"}],
            "target_state": {"entity_id": "npc_mara"},
            "npc_disposition": {"state": "WARY"},
            "npc_knowledge": [{"fact_id": "fact_1"}],
        },
        [event("evt_2", 2), event("evt_1", 1)],
    )

    def retriever(query: RetrievalQuery) -> RetrievalResult:
        captured.append(query)
        return RetrievalResult(memories=[memory("mem_1", 0.9)])

    text, manifest = build_context(
        role=Role.ADJUDICATOR,
        action_class=ActionClass.SOCIAL,
        policy=seed_context_policy(),
        view=view,
        campaign=campaign,
        player=player,
        room=room,
        action_text="Ask Mara about the brass key",
        target_ids=("npc_mara",),
        retriever=retriever,
    )

    assert captured[0].campaign_id == "campaign_1"
    assert captured[0].entity_ids == ["player_1", "cell_1_1", "npc_mara"]
    assert captured[0].recent_event_ids == ["evt_1", "evt_2"]
    assert manifest.entity_ids == ["player_1", "cell_1_1", "npc_mara"]
    assert manifest.event_ids == ["evt_1", "evt_2"]
    assert [reference.id for reference in manifest.memories] == ["mem_1"]
    assert text.index("[current_cell]") < text.index("[recent_events]")
    assert text.index("[recent_events]") < text.index("[semantic_memory]")
    assert text.endswith("[untrusted_player_input]\nAsk Mara about the brass key")


def test_budget_drops_low_scoring_memories_then_oldest_events():
    campaign, player, room = inputs()
    policy = seed_context_policy()
    policy.budget["max_context_tokens"] = 100
    policy.rules[ActionClass.SEARCH].vector_memory.enabled = True
    policy.rules[ActionClass.SEARCH].vector_memory.top_k = 2
    view = FakeView(
        {
            "player_state": {"hp": 8},
            "current_cell": {"description": "x" * 80},
            "visible_entities": [],
        },
        [event("evt_1", 1), event("evt_2", 2)],
    )

    text, manifest = build_context(
        role=Role.ADJUDICATOR,
        action_class=ActionClass.SEARCH,
        policy=policy,
        view=view,
        campaign=campaign,
        player=player,
        room=room,
        action_text="search",
        retriever=lambda _: RetrievalResult(
            memories=[memory("mem_low", 0.1, "x" * 100), memory("mem_high", 0.9, "x" * 100)]
        ),
    )

    assert "MEMORIES_TRUNCATED" in manifest.flags
    assert "RECENT_EVENTS_TRUNCATED" in manifest.flags
    assert manifest.memories == []
    assert manifest.event_ids == []
    assert "[player_state]" in text
    assert "[current_cell]" in text


def test_mandatory_context_is_retained_when_it_exceeds_the_budget():
    campaign, player, room = inputs()
    policy = seed_context_policy()
    policy.budget["max_context_tokens"] = 1
    view = FakeView(
        {"player_state": {"description": "x" * 100}, "current_cell": {}, "visible_entities": []}, []
    )

    text, manifest = build_context(
        role=Role.ADJUDICATOR,
        action_class=ActionClass.MOVE,
        policy=policy,
        view=view,
        campaign=campaign,
        player=player,
        room=room,
        action_text="look",
    )

    assert "[player_state]" in text
    assert "CONTEXT_OVER_BUDGET" in manifest.flags
