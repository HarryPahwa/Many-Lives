"""Pure boss-door and victory mechanics (TDD §4.10 and §13.11)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class DoorOutcome:
    campaign: dict[str, Any]
    items: tuple[dict[str, Any], ...]
    submitted_item_ids: tuple[str, ...]
    unlocked_now: bool


def can_enter_boss(campaign: Mapping[str, Any], destination_cell_id: str) -> bool:
    return (
        destination_cell_id != campaign["boss_cell_id"]
        or bool(campaign["boss_door"]["unlocked"])
    )


def submit_keys(
    campaign: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    *,
    player_id: str,
) -> DoorOutcome:
    updated_campaign = deepcopy(dict(campaign))
    updated_items = deepcopy(list(items))
    door = updated_campaign["boss_door"]
    already = set(door.get("submitted_key_ids", ()))
    eligible = sorted(
        item["entity_id"]
        for item in updated_items
        if item.get("entity_type") == "ITEM"
        and item.get("item", {}).get("subtype") == "KEY"
        and item.get("item", {}).get("status", "ACTIVE") == "ACTIVE"
        and item.get("location", {}).get("kind") == "INVENTORY"
        and item["location"].get("ref_id") == player_id
        and item["entity_id"] not in already
    )
    by_id = {item["entity_id"]: item for item in updated_items}
    for item_id in eligible:
        item = by_id[item_id]
        item["location"] = {"kind": "NONE", "ref_id": None, "slot": None}
        item["item"]["status"] = "CONSUMED"
    door["submitted_key_ids"] = [*door.get("submitted_key_ids", ()), *eligible]
    was_unlocked = bool(door.get("unlocked"))
    door["unlocked"] = was_unlocked or len(set(door["submitted_key_ids"])) >= int(
        door["required_keys"]
    )
    return DoorOutcome(
        updated_campaign,
        tuple(updated_items),
        tuple(eligible),
        bool(door["unlocked"] and not was_unlocked),
    )


def claim_treasure(
    campaign: Mapping[str, Any], *, player_id: str, boss_alive: bool
) -> dict[str, Any]:
    if boss_alive:
        raise ValueError("The treasure remains guarded while the boss lives")
    updated = deepcopy(dict(campaign))
    updated["status"] = "WON"
    updated["winner_player_id"] = player_id
    return updated
