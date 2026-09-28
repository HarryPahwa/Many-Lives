from pathlib import Path

import yaml

from app.domain.types import Archetype, ItemSubtype, RoomEntityRole
from app.world.room_planner import plan_room
from app.world.topology import generate_topology


CONFIG_DIR = Path(__file__).parents[2] / "config"
BALANCE = {
    **yaml.safe_load((CONFIG_DIR / "world_gen.yaml").read_text()),
    **yaml.safe_load((CONFIG_DIR / "runtime_rules.yaml").read_text()),
}


def fixtures(cell_id="cell_0_0", *, spawn="cell_0_0", boss="cell_6_6", keys=()):
    topology = generate_topology(71).to_dict()
    campaign = {"spawn_cell_id": spawn, "boss_cell_id": boss, "topology": topology,
                "ungenerated_key_cell_ids": ["cell_4_4"]}
    cell = {"cell_id": cell_id, "danger_tier": 2,
            "reservations": {"key_item_ids": list(keys)}}
    return cell, campaign


def test_planner_is_deterministic_and_forces_empty_spawn():
    cell, campaign = fixtures()
    first = plan_room(seed=99, cell=cell, campaign=campaign, balance=BALANCE)
    second = plan_room(seed=99, cell=cell, campaign=campaign, balance=BALANCE)
    assert first == second
    assert first.dressing_plan.archetype == Archetype.EMPTY
    assert not first.dressing_plan.entity_slots


def test_boss_has_boss_and_guarded_treasure():
    cell, campaign = fixtures("cell_6_6")
    planned = plan_room(seed=99, cell=cell, campaign=campaign, balance=BALANCE)
    assert planned.dressing_plan.archetype == Archetype.BOSS
    assert planned.dressing_plan.entity_slots[0].role == RoomEntityRole.BOSS
    item = planned.dressing_plan.item_slots[0]
    assert item.subtype_hint == ItemSubtype.TREASURE
    assert item.guard_slot_id == planned.dressing_plan.entity_slots[0].slot_id


def test_reserved_key_retains_identity_and_compatible_carrier():
    cell, campaign = fixtures("cell_3_3", keys=["item_k1"])
    planned = plan_room(seed=13, cell=cell, campaign=campaign, balance=BALANCE)
    assert list(planned.reserved_item_by_slot.values()) == ["item_k1"]
    slot = planned.dressing_plan.item_slots[0]
    assert slot.subtype_hint == ItemSubtype.KEY
    assert slot.holder_slot_id or slot.container_slot_id


def test_every_planned_character_has_loot_and_npc_has_fact():
    for seed in range(40):
        cell, campaign = fixtures("cell_2_3", spawn="cell_0_0")
        planned = plan_room(seed=seed, cell=cell, campaign=campaign, balance=BALANCE)
        roles = [slot.role for slot in planned.dressing_plan.entity_slots]
        if roles:
            assert len(planned.dressing_plan.item_slots) >= len(roles)
        if RoomEntityRole.NPC in roles:
            npc = next(slot for slot in planned.dressing_plan.entity_slots if slot.role == RoomEntityRole.NPC)
            assert planned.entity_mechanics[npc.slot_id].facts
