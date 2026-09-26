"""V1 context-policy defaults and deterministic action classification (TDD §11)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.domain.types import (
    ActionClass,
    ClassRule,
    ContextPolicy,
    MemoryType,
    PolicyCreator,
    PolicyStatus,
    VectorMemoryConfig,
)


def _rule(
    mandatory: list[str],
    *,
    conditional: list[str] | None = None,
    recent_event_window: int = 0,
    vector_memory: VectorMemoryConfig | None = None,
) -> ClassRule:
    return ClassRule(
        mandatory=mandatory,
        conditional=conditional or [],
        recent_event_window=recent_event_window,
        vector_memory=vector_memory or VectorMemoryConfig(enabled=False),
    )


CONTEXT_POLICY_V1 = ContextPolicy(
    _id="context_policy_v1",
    version=1,
    status=PolicyStatus.ACTIVE,
    parent_version=None,
    created_by=PolicyCreator.HUMAN,
    rules={
        ActionClass.MOVE: _rule(["player_state", "current_cell", "visible_entities"]),
        ActionClass.COMBAT: _rule(
            ["player_state", "player_inventory", "target_state", "current_cell"]
        ),
        ActionClass.ITEM: _rule(
            ["player_state", "player_inventory", "current_cell", "visible_entities"]
        ),
        ActionClass.SEARCH: _rule(
            ["player_state", "current_cell", "visible_entities"], recent_event_window=3
        ),
        ActionClass.CREATIVE: _rule(
            ["player_state", "player_inventory", "current_cell", "visible_entities"],
            conditional=["semantic_memory"],
            recent_event_window=5,
            vector_memory=VectorMemoryConfig(
                enabled=True,
                top_k=2,
                memory_types=[MemoryType.ENVIRONMENT, MemoryType.ITEM],
                cell_filter=True,
            ),
        ),
        ActionClass.SOCIAL: _rule(
            [
                "player_state",
                "current_cell",
                "visible_entities",
                "target_state",
                "npc_disposition",
                "npc_knowledge",
            ],
            conditional=["player_inventory"],
            recent_event_window=5,
            vector_memory=VectorMemoryConfig(
                enabled=True,
                top_k=3,
                memory_types=[
                    MemoryType.RELATIONSHIP,
                    MemoryType.DIALOGUE,
                    MemoryType.QUEST,
                    MemoryType.COMBAT,
                ],
                entity_filter=True,
            ),
        ),
        ActionClass.RESUME: _rule(
            ["player_state", "player_inventory", "current_cell", "visible_entities", "known_map"],
            recent_event_window=8,
            vector_memory=VectorMemoryConfig(
                enabled=True,
                top_k=3,
                entity_filter=True,
                cell_filter=True,
            ),
        ),
        ActionClass.NARRATION_DEFAULT: _rule(
            ["current_cell", "visible_entities"], recent_event_window=2
        ),
    },
    budget={"max_context_tokens": 3000},
    promotion_metrics=None,
    created_at="2026-09-26T14:00:00Z",
)


def seed_context_policy() -> ContextPolicy:
    """Return a fresh v1 seed without persisting or promoting any policy."""

    return ContextPolicy.model_validate(CONTEXT_POLICY_V1.model_dump(by_alias=True, mode="json"))


def rule_for(policy: ContextPolicy, action_class: ActionClass) -> ClassRule:
    """Return the typed rule for an action class."""

    return policy.rules[action_class]


def _visible_entities(view: Any) -> Sequence[Mapping[str, Any]]:
    entities = getattr(view, "visible_entities", None)
    if entities is None and isinstance(view, Mapping):
        entities = view.get("visible_entities", [])
    return entities or []


def _is_hostile(entity: Mapping[str, Any]) -> bool:
    character = entity.get("character", {})
    disposition = entity.get("disposition", character.get("disposition"))
    if entity.get("hostile") is True or character.get("hostile") is True:
        return True
    if isinstance(disposition, str):
        return disposition.upper() == "HOSTILE"
    if isinstance(disposition, Mapping):
        if disposition.get("state") == "HOSTILE":
            return True
        return any(
            isinstance(value, Mapping) and value.get("state") == "HOSTILE"
            for value in disposition.values()
        )
    return character.get("faction") == "HOSTILE"


def select_action_class(text: str, view: Any) -> ActionClass:
    """Classify text for context selection only; it never establishes game state."""

    normalized = text.casefold().strip()
    first_word = normalized.split(maxsplit=1)[0] if normalized else ""
    fast_paths = {
        "n": ActionClass.MOVE,
        "s": ActionClass.MOVE,
        "e": ActionClass.MOVE,
        "w": ActionClass.MOVE,
        "north": ActionClass.MOVE,
        "south": ActionClass.MOVE,
        "east": ActionClass.MOVE,
        "west": ActionClass.MOVE,
        "move": ActionClass.MOVE,
        "go": ActionClass.MOVE,
        "flee": ActionClass.MOVE,
        "look": ActionClass.MOVE,
        "l": ActionClass.MOVE,
        "wait": ActionClass.MOVE,
        "attack": ActionClass.COMBAT,
        "hit": ActionClass.COMBAT,
        "fight": ActionClass.COMBAT,
        "cast": ActionClass.COMBAT,
        "take": ActionClass.ITEM,
        "get": ActionClass.ITEM,
        "drop": ActionClass.ITEM,
        "equip": ActionClass.ITEM,
        "unequip": ActionClass.ITEM,
        "use": ActionClass.ITEM,
        "search": ActionClass.SEARCH,
    }
    if first_word in fast_paths:
        return fast_paths[first_word]

    visible = _visible_entities(view)
    named_entities = [
        entity for entity in visible if isinstance(entity.get("name"), str) and entity["name"]
    ]
    if any(
        entity.get("entity_type") == "NPC" and entity["name"].casefold() in normalized
        for entity in named_entities
    ):
        return ActionClass.SOCIAL

    combat_words = ("attack", "hit", "fight", "strike", "stab", "shoot", "cast")
    if any(word in normalized for word in combat_words) and any(
        _is_hostile(entity) and entity.get("name", "").casefold() in normalized
        for entity in named_entities
    ):
        return ActionClass.COMBAT
    return ActionClass.CREATIVE
