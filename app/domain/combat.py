"""Pure deterministic combat mechanics (TDD §4.6 and §13.7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from app.domain.rng import RollRecord, TurnRng


@dataclass(frozen=True)
class AttackOutcome:
    attacker_id: str
    defender_id: str
    dodged: bool
    damage: int
    hp_before: int
    hp_after: int
    effective_dodge: int
    weapon_bonus: int
    armor_bonus: int
    variance: int | None
    rolls: tuple[RollRecord, ...]

    @property
    def killed(self) -> bool:
        return self.hp_after == 0


def equipment_bonuses(
    entity_id: str, items: Iterable[Mapping[str, Any]]
) -> tuple[int, int]:
    attack_bonus = 0
    armor_bonus = 0
    for item in items:
        location = item.get("location", {})
        mechanics = item.get("item", {})
        if (
            location.get("kind") != "EQUIPPED"
            or location.get("ref_id") != entity_id
            or mechanics.get("status", "ACTIVE") != "ACTIVE"
        ):
            continue
        if location.get("slot") == "WEAPON":
            attack_bonus += int(mechanics.get("attack_bonus", 0))
        elif location.get("slot") == "ARMOR":
            armor_bonus += int(mechanics.get("armor_bonus", 0))
    return attack_bonus, armor_bonus


def resolve_attack(
    attacker: Mapping[str, Any],
    defender: Mapping[str, Any],
    *,
    items: Sequence[Mapping[str, Any]],
    rng: TurnRng,
    purpose: str = "combat",
    damage_variance: Sequence[int] = (-1, 0, 1),
) -> AttackOutcome:
    """Resolve one attack and return its complete mechanical record."""
    attacker_stats = attacker["character"]
    defender_stats = defender["character"]
    if attacker_stats.get("status") != "ALIVE":
        raise ValueError("A non-living character cannot attack")
    if defender_stats.get("status") != "ALIVE" or defender_stats["hp"] <= 0:
        raise ValueError("Target must be alive")
    if not damage_variance:
        raise ValueError("damage_variance cannot be empty")

    record_start = len(rng.records)
    effective_dodge = min(40, max(0, int(defender_stats.get("dodge_pct", 0))))
    dodge_roll = rng.roll(purpose, 100)
    attacker_weapon, _ = equipment_bonuses(attacker["entity_id"], items)
    _, defender_armor = equipment_bonuses(defender["entity_id"], items)
    hp_before = int(defender_stats["hp"])
    variance: int | None = None
    damage = 0
    if dodge_roll > effective_dodge:
        variance = int(rng.choice(purpose, tuple(damage_variance)))
        raw = int(attacker_stats["attack"]) + attacker_weapon + variance
        damage = max(1, raw - int(defender_stats["defense"]) - defender_armor)
    hp_after = max(0, hp_before - damage)
    return AttackOutcome(
        attacker_id=attacker["entity_id"],
        defender_id=defender["entity_id"],
        dodged=damage == 0,
        damage=damage,
        hp_before=hp_before,
        hp_after=hp_after,
        effective_dodge=effective_dodge,
        weapon_bonus=attacker_weapon,
        armor_bonus=defender_armor,
        variance=variance,
        rolls=rng.records[record_start:],
    )


def is_hostile(character: Mapping[str, Any], player_id: str) -> bool:
    stats = character.get("character", {})
    if stats.get("status") != "ALIVE":
        return False
    entity_type = character.get("entity_type")
    if entity_type in {"ENEMY", "BOSS"}:
        return True
    if entity_type != "NPC":
        return False
    disposition = stats.get("disposition", {}).get(player_id, {})
    return disposition.get("state") == "HOSTILE"


def hostile_order(
    characters: Iterable[Mapping[str, Any]],
    player_id: str,
    *,
    turn_sequence: int | None = None,
) -> list[Mapping[str, Any]]:
    eligible = []
    for character in characters:
        if not is_hostile(character, player_id):
            continue
        distracted = character.get("character", {}).get("distracted_until_turn")
        if turn_sequence is not None and distracted == turn_sequence:
            continue
        eligible.append(character)
    return sorted(
        eligible,
        key=lambda entity: (
            -int(entity["character"]["speed"]),
            -int(entity["character"]["attack"]),
            entity["entity_id"],
        ),
    )
