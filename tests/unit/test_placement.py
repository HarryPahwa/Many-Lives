"""Tests for spawn, boss, key, and tier placement (TDD §14.2–§14.4)."""

import pytest

from app.world.placement import (
    PlacementError,
    calculate_danger_tiers,
    place_world,
)
from app.world.topology import Topology, generate_topology, is_boundary_cell


@pytest.mark.parametrize("seed", range(20))
def test_production_placement_satisfies_all_world_invariants(seed: int) -> None:
    topology = generate_topology(seed)
    placement = place_world(topology, seed)

    assert is_boundary_cell(placement.spawn_cell_id)
    assert placement.distance_from_spawn[placement.boss_cell_id] >= 5
    assert len(placement.distance_from_spawn) == 49
    assert len(placement.distance_to_boss) == 49

    eligible_interior = [
        cell
        for cell, distance in placement.distance_from_spawn.items()
        if distance >= 5 and not is_boundary_cell(cell)
    ]
    if eligible_interior:
        assert not is_boundary_cell(placement.boss_cell_id)

    keys = placement.key_reservation_cells
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys)) == 6
    assert placement.spawn_cell_id not in keys
    assert placement.boss_cell_id not in keys
    assert all(cell in placement.distance_from_spawn for cell in keys)

    tiers = placement.danger_tiers
    assert tiers[placement.boss_cell_id] == 5
    assert all(type(tier) is int and 1 <= tier <= 5 for tier in tiers.values())
    for left, left_distance in placement.distance_to_boss.items():
        for right, right_distance in placement.distance_to_boss.items():
            if left_distance < right_distance:
                assert tiers[left] >= tiers[right]


def test_placement_is_deterministic() -> None:
    topology = generate_topology(2026)
    assert place_world(topology, 2026) == place_world(topology, 2026)


def test_placement_stream_is_independent_from_topology_rng_consumption() -> None:
    topology = generate_topology(8)
    assert place_world(topology, 99) == place_world(topology, 99)


def test_danger_tier_formula_and_degenerate_graph() -> None:
    assert calculate_danger_tiers({"boss": 0}) == {"boss": 5}
    tiers = calculate_danger_tiers({"boss": 0, "near": 1, "edge": 4, "far": 5})
    assert tiers == {"boss": 5, "near": 5, "edge": 2, "far": 1}


def test_unsatisfiable_topology_raises_instead_of_looping() -> None:
    line = Topology(
        {
            "cell_0_0": ["cell_1_0"],
            "cell_1_0": ["cell_0_0", "cell_2_0"],
            "cell_2_0": ["cell_1_0"],
        }
    )
    with pytest.raises(PlacementError, match="No boss cell"):
        place_world(
            line,
            1,
            width=3,
            height=1,
            min_spawn_boss_distance=5,
            key_reservations=0,
        )


def test_disconnected_topology_is_rejected() -> None:
    disconnected = Topology({"cell_0_0": [], "cell_1_0": []})
    with pytest.raises(PlacementError, match="connected"):
        place_world(
            disconnected,
            1,
            width=2,
            height=1,
            min_spawn_boss_distance=0,
            key_reservations=0,
        )


@pytest.mark.parametrize(
    "distances",
    [{}, {"cell": -1}, {"cell": True}, {"cell": 1.5}],
)
def test_invalid_distance_map_is_rejected(distances: dict) -> None:
    with pytest.raises(ValueError):
        calculate_danger_tiers(distances)
