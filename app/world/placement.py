"""Deterministic spawn, boss, key, and tier placement (TDD §14.2–§14.4)."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.rng import placement_rng
from app.world.topology import Topology, bfs_distances, is_boundary_cell


class PlacementError(RuntimeError):
    """Raised when a topology cannot satisfy the world placement rules."""


@dataclass(frozen=True)
class WorldPlacement:
    spawn_cell_id: str
    boss_cell_id: str
    distance_from_spawn: dict[str, int]
    distance_to_boss: dict[str, int]
    danger_tiers: dict[str, int]
    key_reservation_cells: list[str]


def calculate_danger_tiers(distance_to_boss: dict[str, int]) -> dict[str, int]:
    """Map BFS distances to tiers using the exact §14.3 formula."""
    if not distance_to_boss:
        raise ValueError("distance_to_boss cannot be empty")
    if any(
        isinstance(distance, bool) or not isinstance(distance, int) or distance < 0
        for distance in distance_to_boss.values()
    ):
        raise ValueError("distances must be non-negative integers")
    maximum = max(distance_to_boss.values())
    if maximum == 0:
        return {cell: 5 for cell in distance_to_boss}

    tiers: dict[str, int] = {}
    for cell, distance in distance_to_boss.items():
        # Integer arithmetic is the exact floor((1 - d / d_max) * 5)
        # formula and avoids boundary errors such as floor(0.9999999999).
        closeness_band = ((maximum - distance) * 5) // maximum
        tiers[cell] = max(1, min(5, 1 + closeness_band))
    return tiers


def place_world(
    topology: Topology,
    seed: int,
    *,
    width: int = 7,
    height: int = 7,
    min_spawn_boss_distance: int = 5,
    key_reservations: int = 6,
    max_spawn_attempts: int = 10,
) -> WorldPlacement:
    """Place world landmarks using only the named placement RNG stream."""
    if min_spawn_boss_distance < 0:
        raise ValueError("min_spawn_boss_distance cannot be negative")
    if key_reservations < 0:
        raise ValueError("key_reservations cannot be negative")
    if max_spawn_attempts < 1:
        raise ValueError("max_spawn_attempts must be positive")

    cells = sorted(topology.cells())
    if not cells:
        raise PlacementError("Cannot place landmarks on an empty topology")
    boundary_cells = [
        cell for cell in cells if is_boundary_cell(cell, width=width, height=height)
    ]
    if not boundary_cells:
        raise PlacementError("Topology has no boundary cells")

    reachable = bfs_distances(topology, cells[0])
    if len(reachable) != len(cells):
        raise PlacementError("Topology must be connected")
    if len(cells) - 2 < key_reservations:
        raise PlacementError("Not enough cells for the requested key reservations")

    rng = placement_rng(seed)
    chosen: tuple[str, str, dict[str, int]] | None = None
    for _attempt in range(max_spawn_attempts):
        spawn = rng.choice(boundary_cells)
        distances = bfs_distances(topology, spawn)
        distant = [
            cell
            for cell in cells
            if cell != spawn and distances[cell] >= min_spawn_boss_distance
        ]
        interior = [
            cell
            for cell in distant
            if not is_boundary_cell(cell, width=width, height=height)
        ]
        candidates = interior or distant
        if candidates:
            chosen = spawn, rng.choice(candidates), distances
            break

    if chosen is None:
        raise PlacementError(
            f"No boss cell is at least {min_spawn_boss_distance} steps from a boundary spawn"
        )

    spawn, boss, distance_from_spawn = chosen
    distance_to_boss = bfs_distances(topology, boss)
    available_key_cells = [cell for cell in cells if cell not in {spawn, boss}]
    key_cells = sorted(rng.sample(available_key_cells, key_reservations))
    return WorldPlacement(
        spawn_cell_id=spawn,
        boss_cell_id=boss,
        distance_from_spawn=distance_from_spawn,
        distance_to_boss=distance_to_boss,
        danger_tiers=calculate_danger_tiers(distance_to_boss),
        key_reservation_cells=key_cells,
    )
