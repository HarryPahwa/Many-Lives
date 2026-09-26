"""Pure death, respawn, drop, and XP mechanics (TDD §4.5 and §4.8)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.domain.rng import TurnRng


@dataclass(frozen=True)
class XpAward:
    amount: int
    xp_after: int
    pending_level_ups_after: int
    thresholds_crossed: int


@dataclass(frozen=True)
class DeathOutcome:
    player: dict[str, Any]
    items: tuple[dict[str, Any], ...]
    dropped_item_id: str | None
    death_xp: int
    death_xp_pct: int
    exploration_pct: int
    combat_pct: int
    thresholds_crossed: int


def level_threshold(level: int) -> int:
    if level < 1:
        raise ValueError("level must be positive")
    return 100 * level


def kill_xp(player_level: int, target_level: int, *, boss: bool = False) -> int:
    if boss:
        return 150
    difference = target_level - player_level
    if difference <= -3:
        return 5
    if difference == -2:
        return 10
    if difference == -1:
        return 15
    if difference == 0:
        return 25
    if difference == 1:
        return 35
    if difference == 2:
        return 50
    return 70


def award_xp(character: Mapping[str, Any], amount: int) -> XpAward:
    if amount < 0:
        raise ValueError("XP award cannot be negative")
    level = int(character["level"])
    pending_before = int(character.get("pending_level_ups", 0))
    after = int(character.get("xp", 0)) + amount
    crossed = 0
    virtual_level = level + pending_before
    while after >= level_threshold(virtual_level):
        after -= level_threshold(virtual_level)
        crossed += 1
        virtual_level += 1
    return XpAward(
        amount,
        after,
        pending_before + crossed,
        crossed,
    )


def death_xp(player: Mapping[str, Any]) -> tuple[int, int, int, int]:
    stats = player["character"]
    progress = player["player"]
    exploration_pct = min(10, 2 * int(progress.get("new_cells_since_death", 0)))
    combat_pct = min(10, int(progress.get("damage_dealt_since_death", 0)))
    pct = min(40, 20 + exploration_pct + combat_pct)
    amount = max(1, level_threshold(int(stats["level"])) * pct // 100)
    return amount, pct, exploration_pct, combat_pct


def resolve_player_death(
    player: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    *,
    death_cell_id: str,
    living_hostile_ids: Sequence[str],
    rng: TurnRng,
    turn_sequence: int,
) -> DeathOutcome:
    """Resolve the complete final player/drop state after lethal damage."""
    updated_player = deepcopy(dict(player))
    updated_items = deepcopy(list(items))
    player_id = player["entity_id"]
    carried = sorted(
        (item for item in updated_items if item.get("location", {}).get("kind") == "INVENTORY"
         and item["location"].get("ref_id") == player_id),
        key=lambda item: item["entity_id"],
    )
    equipped = sorted(
        (item for item in updated_items if item.get("location", {}).get("kind") == "EQUIPPED"
         and item["location"].get("ref_id") == player_id),
        key=lambda item: item["entity_id"],
    )
    candidates = carried or equipped
    dropped_id: str | None = None
    if candidates:
        selected = rng.choice("drop", candidates)
        dropped_id = selected["entity_id"]
        if int(selected["item"].get("quantity", 1)) > 1:
            selected["item"]["quantity"] -= 1
            dropped = deepcopy(selected)
            dropped_id = f"{selected['entity_id']}_drop_{turn_sequence}"
            dropped["entity_id"] = dropped_id
            dropped["_id"] = dropped_id
            dropped["item"]["quantity"] = 1
            updated_items.append(dropped)
            selected = dropped
        selected["location"] = {"kind": "CELL", "ref_id": death_cell_id, "slot": None}
        selected["item"]["guarded_by"] = sorted(set(living_hostile_ids))

    amount, pct, exploration_pct, combat_pct = death_xp(player)
    xp = award_xp(player["character"], amount)
    stats = updated_player["character"]
    stats["hp"] = stats["max_hp"]
    stats["mp"] = stats["max_mp"]
    stats["status"] = "ALIVE"
    stats["xp"] = xp.xp_after
    stats["pending_level_ups"] = xp.pending_level_ups_after
    updated_player["location"] = {
        "kind": "CELL",
        "ref_id": updated_player["player"]["spawn_cell_id"],
        "slot": None,
    }
    progress = updated_player["player"]
    progress["deaths"] = int(progress.get("deaths", 0)) + 1
    progress["new_cells_since_death"] = 0
    progress["damage_dealt_since_death"] = 0
    return DeathOutcome(
        updated_player,
        tuple(updated_items),
        dropped_id,
        amount,
        pct,
        exploration_pct,
        combat_pct,
        xp.thresholds_crossed,
    )
