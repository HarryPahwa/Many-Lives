"""Tests for the thin room-dresser model wrapper."""

import pytest

from app.domain.types import EntitySlot, ItemSlot, Role, RoomPlan
from app.harness.model_client import FakeModelClient, ModelOutputError
from app.harness.room_dresser import dress_room
from tests.unit.test_model_client import fixture_dressing


def plan() -> RoomPlan:
    return RoomPlan(
        cell_key="cell_1_1",
        archetype="NPC",
        tier=3,
        entity_slots=[EntitySlot(slot_id="npc_1", role="NPC")],
        item_slots=[ItemSlot(slot_id="item_1", placement="FLOOR")],
        feature_range=(2, 5),
        knowledge_facts=[],
    )


def test_dresser_uses_the_dresser_role_and_stable_prompt_inputs():
    fixture = fixture_dressing()
    client = FakeModelClient({(Role.DRESSER, "default"): fixture})

    result = dress_room(plan(), client=client)
    call = client.calls[0]

    assert result == fixture
    assert call["role"] is Role.DRESSER
    assert call["temperature"] == 0.8
    assert call["max_output_tokens"] == 900
    assert call["timeout_s"] == 20.0
    assert '"danger_tier":"dangerous"' in call["user"]
    assert '"allowed_property_tags"' in call["user"]
    assert "no modern objects" in call["system"]


def test_dresser_propagates_model_output_error():
    with pytest.raises(ModelOutputError):
        dress_room(plan(), client=FakeModelClient(fail_on={Role.DRESSER}))
