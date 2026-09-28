from copy import deepcopy

from app.domain.inventory import drop_item, equip_item, take_item, unequip_item, use_item
from app.domain.rules import resolve_inventory_action
from app.domain.types import ActionIntent, ActionType


def item(entity_id, subtype="TRINKET", *, quantity=1, kind="CELL", ref="cell_0_0", slot=None):
    return {
        "_id": entity_id,
        "entity_id": entity_id,
        "location": {"kind": kind, "ref_id": ref, "slot": slot},
        "item": {
            "subtype": subtype,
            "tier": 1,
            "quantity": quantity,
            "max_stack": 3 if subtype == "MANA_POTION" else 1,
            "stackable": subtype == "MANA_POTION",
            "quest_critical": subtype == "KEY",
            "hidden": False,
            "guarded_by": [],
            "status": "ACTIVE",
        },
    }


def by_id(result, entity_id):
    return next(document for document in result.items if document["entity_id"] == entity_id)


def test_take_merges_stack_without_mutating_input():
    source = item("floor", "MANA_POTION", quantity=2)
    carried = item("bag", "MANA_POTION", quantity=2, kind="INVENTORY", ref="player")
    originals = deepcopy([source, carried])
    result = take_item([source, carried], item_id="floor", player_id="player", current_cell_id="cell_0_0")
    assert result.accepted
    assert by_id(result, "bag")["item"]["quantity"] == 3
    assert by_id(result, "floor")["item"]["quantity"] == 1
    assert by_id(result, "floor")["location"]["kind"] == "INVENTORY"
    assert [source, carried] == originals


def test_take_rejects_hidden_guarded_and_full_capacity():
    hidden = item("hidden")
    hidden["item"]["hidden"] = True
    assert not take_item([hidden], item_id="hidden", player_id="player", current_cell_id="cell_0_0").accepted
    guarded = item("guarded")
    guarded["item"]["guarded_by"] = ["enemy"]
    assert not take_item([guarded], item_id="guarded", player_id="player", current_cell_id="cell_0_0", active_guard_ids=["enemy"]).accepted
    full = [item(f"held{i}", kind="INVENTORY", ref="player") for i in range(6)] + [item("floor")]
    assert not take_item(full, item_id="floor", player_id="player", current_cell_id="cell_0_0").accepted


def test_take_allows_inventory_owned_by_a_dead_character():
    loot = item("loot", kind="INVENTORY", ref="corpse")
    result = take_item([loot], item_id="loot", player_id="player",
                       current_cell_id="cell_0_0", lootable_owner_ids=["corpse"])
    assert result.accepted
    assert by_id(result, "loot")["location"]["ref_id"] == "player"


def test_inventory_action_accepts_unique_partial_name_but_rejects_ambiguity():
    weapon = item("weapon-id", "WEAPON")
    weapon["name"] = "weapon 1"
    intent = ActionIntent(
        action_type=ActionType.TAKE_ITEM,
        actor_id="player",
        params={"query": "weapon"},
    )
    accepted = resolve_inventory_action(
        intent,
        items=[weapon],
        player={},
        current_cell_id="cell_0_0",
        campaign_id="campaign",
        turn_sequence=1,
        turn_id="turn",
    )
    assert accepted.accepted

    second = item("second-weapon", "WEAPON")
    second["name"] = "weapon 2"
    ambiguous = resolve_inventory_action(
        intent,
        items=[weapon, second],
        player={},
        current_cell_id="cell_0_0",
        campaign_id="campaign",
        turn_sequence=1,
        turn_id="turn",
    )
    assert not ambiguous.accepted


def test_drop_splits_stack_and_equip_swaps():
    potion = item("potions", "MANA_POTION", quantity=3, kind="INVENTORY", ref="player")
    dropped = drop_item([potion], item_id="potions", player_id="player", current_cell_id="cell_2_2", quantity=1, new_item_id="drop")
    assert by_id(dropped, "potions")["item"]["quantity"] == 2
    assert by_id(dropped, "drop")["location"]["ref_id"] == "cell_2_2"
    old = item("old", "WEAPON", kind="EQUIPPED", ref="player", slot="WEAPON")
    new = item("new", "WEAPON", kind="INVENTORY", ref="player")
    equipped = equip_item([old, new], item_id="new", player_id="player")
    assert by_id(equipped, "new")["location"]["kind"] == "EQUIPPED"
    assert by_id(equipped, "old")["location"]["kind"] == "INVENTORY"


def test_unequip_drops_when_carried_slots_are_full():
    equipped = item("sword", "WEAPON", kind="EQUIPPED", ref="player", slot="WEAPON")
    carried = [item(f"held{i}", kind="INVENTORY", ref="player") for i in range(6)]
    result = unequip_item([equipped, *carried], item_id="sword", player_id="player", current_cell_id="cell_1_1")
    assert by_id(result, "sword")["location"] == {"kind": "CELL", "ref_id": "cell_1_1", "slot": None}
    assert result.event_types[-1] == "ITEM_DROPPED"


def test_use_potion_caps_mp_and_rejects_quest_item():
    potion = item("potion", "MANA_POTION", quantity=2, kind="INVENTORY", ref="player")
    result = use_item([potion], item_id="potion", player_id="player", current_mp=8, max_mp=10)
    assert result.player_mp == 10
    assert by_id(result, "potion")["item"]["quantity"] == 1
    key = item("key", "KEY", kind="INVENTORY", ref="player")
    assert not use_item([key], item_id="key", player_id="player", current_mp=0, max_mp=10).accepted
