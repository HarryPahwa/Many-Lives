"""Deterministic mechanical room planning (TDD §14.5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.domain.rng import cell_plan_rng
from app.domain.types import (
    Archetype,
    EntitySlot,
    Fact,
    ItemSlot,
    ItemSlotPlacement,
    ItemSubtype,
    RoomEntityRole,
    RoomPlan,
)
from app.world.topology import Topology, bfs_distances


@dataclass(frozen=True)
class CharacterMechanics:
    role: RoomEntityRole
    stats: Mapping[str, int]
    faction: str
    facts: tuple[Fact, ...] = ()


@dataclass(frozen=True)
class ItemMechanics:
    subtype: ItemSubtype
    tier: int
    quantity: int
    stackable: bool
    max_stack: int
    quest_critical: bool
    attack_bonus: int = 0
    armor_bonus: int = 0
    effects: tuple[Mapping[str, Any], ...] = ()
    hidden: bool = False
    concealment_dc: int | None = None


@dataclass(frozen=True)
class PlannedRoom:
    dressing_plan: RoomPlan
    entity_mechanics: Mapping[str, CharacterMechanics]
    item_mechanics: Mapping[str, ItemMechanics]
    reserved_item_by_slot: Mapping[str, str]


def _weighted_choice(rng, weights: Mapping[str, int]) -> str:
    total = sum(weights.values())
    pick = rng.randrange(total)
    running = 0
    for value, weight in weights.items():
        running += weight
        if pick < running:
            return value
    raise RuntimeError("Invalid weight table")


def _nearest_hint_cell(campaign: Mapping[str, Any], source: str) -> str:
    candidates = []
    for cell_id, neighbors in campaign["topology"].items():
        del neighbors
        # Campaign creation stores all reserved key cells in key entity locations,
        # but the planner receives the current cell only. Prefer the boss when no
        # optional reservation catalog was supplied by the service.
        if cell_id in campaign.get("ungenerated_key_cell_ids", ()):
            candidates.append(cell_id)
    if not candidates:
        return campaign["boss_cell_id"]
    distances = bfs_distances(Topology(dict(campaign["topology"])), source)
    return min(candidates, key=lambda cell_id: (distances[cell_id], cell_id))


def plan_room(
    *,
    seed: int,
    cell: Mapping[str, Any],
    campaign: Mapping[str, Any],
    balance: Mapping[str, Any],
) -> PlannedRoom:
    """Choose all room mechanics without prose or canonical entity IDs."""
    cell_key = cell["cell_id"]
    tier = int(cell["danger_tier"])
    rng = cell_plan_rng(seed, cell_key)
    generation = balance["generation"]
    reserved_ids = list(cell.get("reservations", {}).get("key_item_ids", ()))

    carrier: str | None = None
    if cell_key == campaign["spawn_cell_id"]:
        archetype = Archetype.EMPTY
    elif cell_key == campaign["boss_cell_id"]:
        archetype = Archetype.BOSS
    elif reserved_ids:
        carrier = _weighted_choice(rng, generation["key_carrier_weights"])
        archetype = {
            "CONTAINER": Archetype.ITEM,
            "ENEMY": Archetype.ENEMY_WITH_ITEM,
            "NPC": Archetype.NPC,
        }[carrier]
    else:
        archetype = Archetype(
            _weighted_choice(rng, generation["archetype_weights"])
        )

    entity_slots: list[EntitySlot] = []
    item_slots: list[ItemSlot] = []
    facts: list[Fact] = []
    entity_mechanics: dict[str, CharacterMechanics] = {}
    item_mechanics: dict[str, ItemMechanics] = {}
    reserved_by_slot: dict[str, str] = {}

    def add_character(role: RoomEntityRole) -> str:
        slot_id = f"entity_{len(entity_slots) + 1}"
        entity_slots.append(EntitySlot(slot_id=slot_id, role=role))
        row_key: int | str = "boss" if role == RoomEntityRole.BOSS else tier
        stats = dict(generation["character_stats"][row_key])
        role_facts: tuple[Fact, ...] = ()
        if role == RoomEntityRole.NPC:
            adjustments = generation["npc_adjustments"]
            stats["attack"] = max(adjustments["minimum"], stats["attack"] + adjustments["attack"])
            stats["dodge_pct"] = max(
                adjustments["minimum"], stats["dodge_pct"] + adjustments["dodge_pct"]
            )
            fact = Fact(
                fact_id=f"fact_{cell_key.removeprefix('cell_')}_{len(facts) + 1}",
                type="CELL_HINT",
                subject_cell_id=_nearest_hint_cell(campaign, cell_key),
                hint="A sealed passage lies deeper in the dungeon.",
            )
            facts.append(fact)
            role_facts = (fact,)
        entity_mechanics[slot_id] = CharacterMechanics(
            role=role,
            stats=stats,
            faction="HOSTILE" if role in {RoomEntityRole.ENEMY, RoomEntityRole.BOSS} else "NEUTRAL",
            facts=role_facts,
        )
        return slot_id

    def add_item(
        *,
        placement: ItemSlotPlacement,
        subtype: ItemSubtype | None = None,
        holder: str | None = None,
        guard: str | None = None,
        feature_slot: str | None = None,
        reserved_id: str | None = None,
    ) -> str:
        slot_id = f"item_{len(item_slots) + 1}"
        if subtype is None:
            subtype = ItemSubtype(_weighted_choice(rng, generation["loot_weights"]))
        item_slots.append(
            ItemSlot(
                slot_id=slot_id,
                subtype_hint=subtype,
                placement=placement,
                container_slot_id=feature_slot,
                holder_slot_id=holder,
                guard_slot_id=guard,
            )
        )
        quantity = rng.randint(1, 2) if subtype == ItemSubtype.MANA_POTION else 1
        bonus = balance["inventory"]["tier_bonus"][tier]
        item_mechanics[slot_id] = ItemMechanics(
            subtype=subtype,
            tier=tier,
            quantity=quantity,
            stackable=subtype == ItemSubtype.MANA_POTION,
            max_stack=balance["inventory"]["max_stack"] if subtype == ItemSubtype.MANA_POTION else 1,
            quest_critical=subtype in {ItemSubtype.KEY, ItemSubtype.TREASURE, ItemSubtype.QUEST_ITEM},
            attack_bonus=bonus if subtype == ItemSubtype.WEAPON else 0,
            armor_bonus=bonus if subtype == ItemSubtype.ARMOR else 0,
            effects=(
                ({"type": "RESTORE_MP", "amount": balance["inventory"]["mana_potion_restore"]},)
                if subtype == ItemSubtype.MANA_POTION
                else ()
            ),
            hidden=placement == ItemSlotPlacement.HIDDEN,
            concealment_dc=10 + tier if placement == ItemSlotPlacement.HIDDEN else None,
        )
        if reserved_id is not None:
            reserved_by_slot[slot_id] = reserved_id
        return slot_id

    if archetype == Archetype.EMPTY:
        if rng.random() < 0.5 and cell_key != campaign["spawn_cell_id"]:
            add_item(placement=ItemSlotPlacement.FLOOR, subtype=ItemSubtype.TRINKET)
    elif archetype == Archetype.ENEMY:
        enemy = add_character(RoomEntityRole.ENEMY)
        add_item(placement=ItemSlotPlacement.HELD, holder=enemy)
    elif archetype == Archetype.NPC:
        npc = add_character(RoomEntityRole.NPC)
        add_item(placement=ItemSlotPlacement.HELD, holder=npc)
    elif archetype == Archetype.ITEM:
        placement = rng.choice(
            [ItemSlotPlacement.FLOOR, ItemSlotPlacement.CONTAINER, ItemSlotPlacement.HIDDEN]
        )
        feature = None if placement == ItemSlotPlacement.FLOOR else "feature_item_1"
        add_item(placement=placement, feature_slot=feature)
    elif archetype == Archetype.ENEMY_WITH_ITEM:
        enemy = add_character(RoomEntityRole.ENEMY)
        add_item(placement=ItemSlotPlacement.GUARDED, guard=enemy)
    elif archetype == Archetype.NPC_WITH_ITEM:
        npc = add_character(RoomEntityRole.NPC)
        add_item(placement=ItemSlotPlacement.HELD, holder=npc)
        add_item(placement=ItemSlotPlacement.FLOOR)
    elif archetype == Archetype.ENEMY_AND_NPC:
        enemy = add_character(RoomEntityRole.ENEMY)
        npc = add_character(RoomEntityRole.NPC)
        add_item(placement=ItemSlotPlacement.HELD, holder=enemy)
        add_item(placement=ItemSlotPlacement.HELD, holder=npc)
    elif archetype == Archetype.BOSS:
        boss = add_character(RoomEntityRole.BOSS)
        add_item(
            placement=ItemSlotPlacement.GUARDED,
            subtype=ItemSubtype.TREASURE,
            guard=boss,
        )

    if reserved_ids:
        # Replace generic loot with the reserved key where possible.
        item_slots.clear()
        item_mechanics.clear()
        reserved_by_slot.clear()
        for index, reserved_id in enumerate(reserved_ids, start=1):
            if carrier == "CONTAINER":
                add_item(
                    placement=ItemSlotPlacement.CONTAINER,
                    subtype=ItemSubtype.KEY,
                    feature_slot=f"feature_key_{index}",
                    reserved_id=reserved_id,
                )
            else:
                holder = entity_slots[0].slot_id
                add_item(
                    placement=ItemSlotPlacement.HELD,
                    subtype=ItemSubtype.KEY,
                    holder=holder,
                    reserved_id=reserved_id,
                )

    minimum_features, maximum_features = generation["room_feature_range"]
    if archetype == Archetype.BOSS:
        minimum_features = max(1, minimum_features)
    plan = RoomPlan(
        cell_key=cell_key,
        archetype=archetype,
        tier=tier,
        entity_slots=entity_slots,
        item_slots=item_slots,
        feature_range=(minimum_features, maximum_features),
        knowledge_facts=facts,
    )
    return PlannedRoom(plan, entity_mechanics, item_mechanics, reserved_by_slot)
