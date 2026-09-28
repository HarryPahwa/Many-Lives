from pathlib import Path

import yaml

from app.world.fallback import build_fallback_dressing
from app.world.room_planner import plan_room
from app.world.room_validation import validate_room_dressing
from app.world.topology import generate_topology


CONFIG_DIR = Path(__file__).parents[2] / "config"
BALANCE = {
    **yaml.safe_load((CONFIG_DIR / "world_gen.yaml").read_text()),
    **yaml.safe_load((CONFIG_DIR / "runtime_rules.yaml").read_text()),
}


def test_fallback_features_are_not_the_same_pair_every_room():
    topology = generate_topology(2).to_dict()
    campaign = {"spawn_cell_id": "cell_0_0", "boss_cell_id": "cell_6_6",
                "topology": topology, "ungenerated_key_cell_ids": []}
    cell = {"cell_id": "cell_2_2", "danger_tier": 2, "reservations": {"key_item_ids": []}}
    planned = plan_room(seed=1, cell=cell, campaign=campaign, balance=BALANCE)
    seen: set[frozenset[str]] = set()
    names: set[str] = set()
    for seed in range(12):
        dressing = build_fallback_dressing(planned, seed=seed)
        room_names = frozenset(feature.name for feature in dressing.features)
        seen.add(room_names)
        names.update(room_names)
    assert len(seen) > 1
    assert len(names) > 4


def test_fallback_is_deterministic_and_valid_for_many_plans():
    topology = generate_topology(2).to_dict()
    campaign = {"spawn_cell_id": "cell_0_0", "boss_cell_id": "cell_6_6",
                "topology": topology, "ungenerated_key_cell_ids": []}
    for seed in range(20):
        cell = {"cell_id": "cell_2_2", "danger_tier": 2,
                "reservations": {"key_item_ids": []}}
        planned = plan_room(seed=seed, cell=cell, campaign=campaign, balance=BALANCE)
        first = build_fallback_dressing(planned, seed=seed)
        assert first == build_fallback_dressing(planned, seed=seed)
        assert validate_room_dressing(planned, first) is first
