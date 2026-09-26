"""Read-only bounded context construction (TDD §11)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from math import ceil
from typing import Any, Protocol

from app.domain.types import (
    ActionClass,
    ContextManifest,
    ContextPolicy,
    Event,
    MemoryReference,
    RetrievalQuery,
    RetrievalResult,
    Role,
)
from app.harness.context_policy import rule_for


class ContextView(Protocol):
    """Read-model boundary needed by this module; it has no persistence dependency."""

    def current_state(
        self,
        *,
        campaign: Mapping[str, Any],
        player: Mapping[str, Any],
        room: Mapping[str, Any],
        target_ids: Sequence[str],
    ) -> Mapping[str, Any]: ...

    def recent_events(
        self,
        *,
        campaign_id: str,
        player_id: str,
        room_id: str,
        target_ids: Sequence[str],
        limit: int,
    ) -> Sequence[Event]: ...


Retriever = Callable[[RetrievalQuery], RetrievalResult]

_COMPONENT_ORDER = (
    "system_contract",
    "player_state",
    "player_inventory",
    "current_cell",
    "visible_entities",
    "target_state",
    "npc_disposition",
    "recent_events",
    "semantic_memory",
    "npc_knowledge",
    "known_map",
    "active_quests",
)

_DROPPABLE_CONDITIONAL = {"semantic_memory", "active_quests"}


def _identifier(record: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    raise ValueError(f"Missing identifier; expected one of {keys}")


def _unique_ids(ids: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(identifier for identifier in ids if identifier))


def _event_key(event: Event) -> tuple[int, int, str]:
    return event.turn_sequence, event.event_index, event.event_id


def _json_block(component: str, value: Any) -> str:
    return f"[{component}]\n{json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)}"


def _system_contract(role: Role) -> dict[str, str]:
    return {
        "role": role.value,
        "authority": "Current deterministic state is authoritative; model output cannot mutate it.",
    }


def _references_inventory(action_text: str | None, inventory: Any, target_ids: Sequence[str]) -> bool:
    if any(target_ids):
        return True
    if not action_text:
        return False
    text = action_text.casefold()
    items = inventory if isinstance(inventory, Sequence) and not isinstance(inventory, str) else []
    return any(
        isinstance(item, Mapping)
        and isinstance(item.get("name"), str)
        and item["name"].casefold() in text
        for item in items
    )


def _render_memories(memories: Sequence[Any]) -> str:
    rows = [
        {
            "id": memory.memory_id,
            "score": memory.score,
            "text": f"historical (turn {memory.created_turn}); current state takes precedence: {memory.text}",
        }
        for memory in memories
    ]
    return _json_block("semantic_memory", rows)


def _render_events(events: Sequence[Event]) -> str:
    return _json_block(
        "recent_events",
        [
            {
                "event_id": event.event_id,
                "turn_sequence": event.turn_sequence,
                "summary": event.summary,
            }
            for event in events
        ],
    )


def build_context(
    *,
    role: Role,
    action_class: ActionClass,
    policy: ContextPolicy,
    view: ContextView,
    campaign: Mapping[str, Any],
    player: Mapping[str, Any],
    room: Mapping[str, Any],
    action_text: str | None,
    target_ids: Sequence[str] = (),
    retriever: Retriever | None = None,
) -> tuple[str, ContextManifest]:
    """Compose bounded prompt context from caller-provided read dependencies only."""

    rule = rule_for(policy, action_class)
    campaign_id = _identifier(campaign, "campaign_id", "_id", "id")
    player_id = _identifier(player, "entity_id", "player_id", "_id", "id")
    room_id = _identifier(room, "cell_id", "cell_key", "_id", "id")
    target_ids = tuple(_unique_ids(target_ids))
    entity_ids = _unique_ids([player_id, room_id, *target_ids])
    state = view.current_state(campaign=campaign, player=player, room=room, target_ids=target_ids)

    selected = {"system_contract", *rule.mandatory}
    conditional = set(rule.conditional)
    if "player_inventory" in conditional and _references_inventory(
        action_text, state.get("player_inventory"), target_ids
    ):
        selected.add("player_inventory")
    if "semantic_memory" in conditional and rule.vector_memory.enabled:
        selected.add("semantic_memory")
    if rule.vector_memory.enabled:
        selected.add("semantic_memory")

    events = sorted(
        view.recent_events(
            campaign_id=campaign_id,
            player_id=player_id,
            room_id=room_id,
            target_ids=target_ids,
            limit=rule.recent_event_window,
        ),
        key=_event_key,
    ) if rule.recent_event_window else []
    if events:
        selected.add("recent_events")

    retrieval = RetrievalResult()
    flags: list[str] = []
    if "semantic_memory" in selected and retriever is not None:
        query = RetrievalQuery(
            campaign_id=campaign_id,
            query_text=action_text or "",
            config=rule.vector_memory,
            entity_ids=entity_ids,
            cell_id=room_id,
            recent_event_ids=[event.event_id for event in events],
        )
        try:
            retrieval = retriever(query)
        except Exception:
            flags.append("VECTOR_UNAVAILABLE")
        else:
            flags.extend(retrieval.flags)

    memories = list(retrieval.memories)

    def render() -> tuple[str, list[str]]:
        blocks: list[str] = []
        components: list[str] = []
        for component in _COMPONENT_ORDER:
            if component not in selected:
                continue
            components.append(component)
            if component == "system_contract":
                blocks.append(_json_block(component, _system_contract(role)))
            elif component == "recent_events":
                blocks.append(_render_events(events))
            elif component == "semantic_memory":
                blocks.append(_render_memories(memories))
            else:
                blocks.append(_json_block(component, state.get(component, {})))
        if action_text is not None:
            blocks.append(f"[untrusted_player_input]\n{action_text}")
        return "\n\n".join(blocks), components

    text, components = render()
    budget = policy.budget["max_context_tokens"]
    while ceil(len(text) / 4) > budget and memories:
        lowest = min(range(len(memories)), key=lambda index: (memories[index].score, -index))
        memories.pop(lowest)
        if "MEMORIES_TRUNCATED" not in flags:
            flags.append("MEMORIES_TRUNCATED")
        text, components = render()
    while ceil(len(text) / 4) > budget and events:
        events.pop(0)
        if "RECENT_EVENTS_TRUNCATED" not in flags:
            flags.append("RECENT_EVENTS_TRUNCATED")
        text, components = render()
    if ceil(len(text) / 4) > budget and "known_map" in selected:
        selected.remove("known_map")
        flags.append("KNOWN_MAP_OMITTED")
        text, components = render()
    for component in _COMPONENT_ORDER:
        if ceil(len(text) / 4) <= budget:
            break
        if (
            component in conditional | _DROPPABLE_CONDITIONAL
            and component not in rule.mandatory
            and component in selected
        ):
            selected.remove(component)
            flags.append("CONDITIONAL_COMPONENTS_OMITTED")
            text, components = render()
    estimate = ceil(len(text) / 4)
    if estimate > budget:
        flags.append("CONTEXT_OVER_BUDGET")

    manifest = ContextManifest(
        policy_version=policy.version,
        action_class=action_class,
        components=components,
        entity_ids=entity_ids,
        event_ids=[event.event_id for event in events],
        memories=[MemoryReference(id=memory.memory_id, score=memory.score) for memory in memories],
        estimated_tokens=estimate,
        flags=_unique_ids(flags),
    )
    return text, manifest
