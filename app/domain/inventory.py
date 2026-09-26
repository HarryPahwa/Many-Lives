"""Pure inventory and item mechanics (TDD §4.9 and §13.4)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

CARRIED_CAPACITY = 6
EQUIPMENT_SUBTYPES = {"WEAPON", "ARMOR"}


@dataclass(frozen=True)
class InventoryResult:
    accepted: bool
    reason: str | None
    items: tuple[dict[str, Any], ...]
    event_types: tuple[str, ...] = ()
    player_mp: int | None = None


def _reject(items: Sequence[Mapping[str, Any]], reason: str) -> InventoryResult:
    return InventoryResult(False, reason, tuple(deepcopy(list(items))))


def _copies(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    documents = deepcopy(list(items))
    validate_inventory(documents)
    return documents


def _find(items: Iterable[dict[str, Any]], entity_id: str) -> dict[str, Any]:
    try:
        return next(item for item in items if item["entity_id"] == entity_id)
    except StopIteration as exc:
        raise ValueError(f"Item not found: {entity_id}") from exc


def _location(item: Mapping[str, Any], kind: str, ref_id: str) -> bool:
    location = item.get("location", {})
    return location.get("kind") == kind and location.get("ref_id") == ref_id


def _carried(items: Iterable[Mapping[str, Any]], player_id: str) -> list[Mapping[str, Any]]:
    return [item for item in items if _location(item, "INVENTORY", player_id)]


def validate_inventory(items: Sequence[Mapping[str, Any]]) -> None:
    """Validate location, stack, identity, and equipment invariants."""
    ids: set[str] = set()
    equipped: set[tuple[str, str]] = set()
    for item in items:
        entity_id = item.get("entity_id")
        if not entity_id or entity_id in ids:
            raise ValueError("Every item must have one unique entity_id")
        ids.add(entity_id)
        location = item.get("location")
        if not isinstance(location, Mapping) or not location.get("kind"):
            raise ValueError(f"Item {entity_id} must have exactly one location")
        mechanics = item.get("item", {})
        quantity = mechanics.get("quantity", 1)
        max_stack = mechanics.get("max_stack", 1)
        if quantity < 1 or max_stack < 1 or quantity > max_stack:
            raise ValueError(f"Item {entity_id} has an invalid stack quantity")
        if not mechanics.get("stackable", False) and quantity != 1:
            raise ValueError(f"Non-stackable item {entity_id} has quantity != 1")
        if location["kind"] == "EQUIPPED":
            key = (str(location.get("ref_id")), str(location.get("slot")))
            if key in equipped:
                raise ValueError(f"Equipment slot {key[1]} is occupied twice")
            equipped.add(key)


def take_item(
    items: Sequence[Mapping[str, Any]], *, item_id: str, player_id: str,
    current_cell_id: str, quantity: int | None = None,
    active_guard_ids: Iterable[str] = (), open_container_ids: Iterable[str] = (),
    lootable_owner_ids: Iterable[str] = (),
    new_item_id: str | None = None,
) -> InventoryResult:
    documents = _copies(items)
    item = _find(documents, item_id)
    mechanics = item["item"]
    requested = mechanics["quantity"] if quantity is None else quantity
    if requested < 1 or requested > mechanics["quantity"]:
        return _reject(items, "Invalid quantity.")
    if mechanics.get("hidden"):
        return _reject(items, "That item has not been revealed.")
    if set(mechanics.get("guarded_by", ())) & set(active_guard_ids):
        return _reject(items, "That item is actively guarded.")
    location = item["location"]
    reachable = _location(item, "CELL", current_cell_id)
    reachable |= location.get("kind") == "CONTAINER" and location.get("ref_id") in set(open_container_ids)
    reachable |= location.get("kind") == "INVENTORY" and location.get("ref_id") in set(
        lootable_owner_ids
    )
    if not reachable:
        return _reject(items, "That item is not reachable.")
    compatible = [candidate for candidate in _carried(documents, player_id)
                  if candidate["item"].get("stackable")
                  and candidate["item"].get("subtype") == mechanics.get("subtype")
                  and candidate["item"].get("tier") == mechanics.get("tier")
                  and candidate["item"]["quantity"] < candidate["item"]["max_stack"]]
    remaining = requested
    for stack in compatible:
        moved = min(stack["item"]["max_stack"] - stack["item"]["quantity"], remaining)
        stack["item"]["quantity"] += moved
        remaining -= moved
        if not remaining:
            break
    if remaining and len(_carried(documents, player_id)) >= CARRIED_CAPACITY:
        return _reject(items, "No carried slot or matching stack has room.")
    if requested == mechanics["quantity"]:
        if remaining:
            mechanics["quantity"] = remaining
            item["location"] = {"kind": "INVENTORY", "ref_id": player_id, "slot": None}
        else:
            item["location"] = {"kind": "NONE", "ref_id": None, "slot": None}
            mechanics["status"] = "CONSUMED"
    else:
        mechanics["quantity"] -= requested
        if remaining:
            if not new_item_id:
                return _reject(items, "A new item ID is required to split a stack.")
            split = deepcopy(item)
            split["entity_id"] = new_item_id
            split["_id"] = new_item_id
            split["item"]["quantity"] = remaining
            split["location"] = {"kind": "INVENTORY", "ref_id": player_id, "slot": None}
            documents.append(split)
    validate_inventory(documents)
    return InventoryResult(True, None, tuple(documents), ("ITEM_TRANSFERRED",))


def drop_item(
    items: Sequence[Mapping[str, Any]], *, item_id: str, player_id: str,
    current_cell_id: str, quantity: int | None = None,
    new_item_id: str | None = None,
) -> InventoryResult:
    documents = _copies(items)
    item = _find(documents, item_id)
    if not _location(item, "INVENTORY", player_id):
        return _reject(items, "Only carried items can be dropped.")
    mechanics = item["item"]
    requested = mechanics["quantity"] if quantity is None else quantity
    if requested < 1 or requested > mechanics["quantity"]:
        return _reject(items, "Invalid quantity.")
    floor = {"kind": "CELL", "ref_id": current_cell_id, "slot": None}
    if requested == mechanics["quantity"]:
        item["location"] = floor
        mechanics["guarded_by"] = []
    else:
        if not new_item_id:
            return _reject(items, "A new item ID is required to split a stack.")
        mechanics["quantity"] -= requested
        split = deepcopy(item)
        split["entity_id"] = new_item_id
        split["_id"] = new_item_id
        split["item"]["quantity"] = requested
        split["item"]["guarded_by"] = []
        split["location"] = floor
        documents.append(split)
    validate_inventory(documents)
    return InventoryResult(True, None, tuple(documents), ("ITEM_DROPPED",))


def equip_item(items: Sequence[Mapping[str, Any]], *, item_id: str, player_id: str) -> InventoryResult:
    documents = _copies(items)
    item = _find(documents, item_id)
    subtype = item["item"].get("subtype")
    if subtype not in EQUIPMENT_SUBTYPES:
        return _reject(items, "Only weapons and armor can be equipped.")
    if not _location(item, "INVENTORY", player_id):
        return _reject(items, "The item must be carried before it can be equipped.")
    if item["item"].get("stackable") or item["item"].get("quantity") != 1:
        return _reject(items, "Equipment must be a single non-stackable item.")
    occupied = next((other for other in documents
                     if _location(other, "EQUIPPED", player_id)
                     and other["location"].get("slot") == subtype), None)
    item["location"] = {"kind": "EQUIPPED", "ref_id": player_id, "slot": subtype}
    if occupied is not None:
        occupied["location"] = {"kind": "INVENTORY", "ref_id": player_id, "slot": None}
    validate_inventory(documents)
    return InventoryResult(True, None, tuple(documents), ("ITEM_EQUIPPED",))


def unequip_item(
    items: Sequence[Mapping[str, Any]], *, item_id: str, player_id: str,
    current_cell_id: str,
) -> InventoryResult:
    documents = _copies(items)
    item = _find(documents, item_id)
    if not _location(item, "EQUIPPED", player_id):
        return _reject(items, "That item is not equipped by the player.")
    if len(_carried(documents, player_id)) < CARRIED_CAPACITY:
        item["location"] = {"kind": "INVENTORY", "ref_id": player_id, "slot": None}
        events = ("ITEM_UNEQUIPPED", "ITEM_TRANSFERRED")
    else:
        item["location"] = {"kind": "CELL", "ref_id": current_cell_id, "slot": None}
        events = ("ITEM_UNEQUIPPED", "ITEM_DROPPED")
    validate_inventory(documents)
    return InventoryResult(True, None, tuple(documents), events)


def use_item(
    items: Sequence[Mapping[str, Any]], *, item_id: str, player_id: str,
    current_mp: int, max_mp: int,
) -> InventoryResult:
    documents = _copies(items)
    item = _find(documents, item_id)
    mechanics = item["item"]
    if not _location(item, "INVENTORY", player_id):
        return _reject(items, "Only carried items can be used.")
    if mechanics.get("quest_critical"):
        return _reject(items, "Quest-critical items cannot be consumed.")
    if mechanics.get("subtype") != "MANA_POTION":
        return _reject(items, "That item has no supported use.")
    restored_mp = min(max_mp, current_mp + 4)
    if mechanics["quantity"] > 1:
        mechanics["quantity"] -= 1
    else:
        mechanics["status"] = "CONSUMED"
        item["location"] = {"kind": "NONE", "ref_id": None, "slot": None}
    validate_inventory(documents)
    return InventoryResult(True, None, tuple(documents), ("ITEM_CONSUMED",), restored_mp)
