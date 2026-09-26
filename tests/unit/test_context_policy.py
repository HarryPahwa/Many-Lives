"""Tests for deterministic v1 context policy selection."""

from app.domain.types import ActionClass
from app.harness.context_policy import CONTEXT_POLICY_V1, rule_for, seed_context_policy, select_action_class


def test_v1_policy_has_the_tdd_defaults_and_typed_lookup():
    creative = rule_for(CONTEXT_POLICY_V1, ActionClass.CREATIVE)

    assert CONTEXT_POLICY_V1.policy_id == "context_policy_v1"
    assert CONTEXT_POLICY_V1.budget["max_context_tokens"] == 3000
    assert creative.vector_memory.top_k == 2
    assert creative.vector_memory.cell_filter is True


def test_seed_is_idempotent_and_returns_an_independent_policy():
    first = seed_context_policy()
    second = seed_context_policy()
    first.budget["max_context_tokens"] = 1

    assert second == CONTEXT_POLICY_V1
    assert second.budget["max_context_tokens"] == 3000


def test_action_class_selection_uses_fast_paths_and_visible_entities():
    view = {
        "visible_entities": [
            {"entity_id": "npc_mara", "entity_type": "NPC", "name": "Mara"},
            {"entity_id": "enemy_goblin", "name": "Goblin", "hostile": True},
        ]
    }

    assert select_action_class("search the room", view) is ActionClass.SEARCH
    assert select_action_class("ask Mara about the crypt", view) is ActionClass.SOCIAL
    assert select_action_class("strike Goblin", view) is ActionClass.COMBAT
    assert select_action_class("build a barricade", view) is ActionClass.CREATIVE
