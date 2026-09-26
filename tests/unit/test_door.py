import pytest

from app.domain.door import can_enter_boss, claim_treasure, submit_keys


def campaign():
    return {"boss_cell_id": "cell_6_6", "status": "ACTIVE", "winner_player_id": None,
            "boss_door": {"required_keys": 3, "submitted_key_ids": [], "unlocked": False}}


def key(entity_id, owner="player"):
    return {"entity_id": entity_id, "entity_type": "ITEM",
            "location": {"kind": "INVENTORY", "ref_id": owner, "slot": None},
            "item": {"subtype": "KEY", "status": "ACTIVE", "quest_critical": True}}


def test_distinct_key_submission_accumulates_and_unlocks_once():
    first = submit_keys(campaign(), [key("k1"), key("k2")], player_id="player")
    assert first.submitted_item_ids == ("k1", "k2")
    assert not first.unlocked_now and not can_enter_boss(first.campaign, "cell_6_6")
    second = submit_keys(first.campaign, [*first.items, key("k3")], player_id="player")
    assert second.unlocked_now and can_enter_boss(second.campaign, "cell_6_6")
    repeated = submit_keys(second.campaign, second.items, player_id="player")
    assert repeated.submitted_item_ids == () and not repeated.unlocked_now


def test_treasure_requires_dead_boss():
    with pytest.raises(ValueError):
        claim_treasure(campaign(), player_id="player", boss_alive=True)
    won = claim_treasure(campaign(), player_id="player", boss_alive=False)
    assert won["status"] == "WON" and won["winner_player_id"] == "player"
