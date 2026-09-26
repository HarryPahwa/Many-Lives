"""Tests for randomized-Kruskal topology generation (TDD §14.1)."""

import pytest

from app.world.topology import (
    MINIMAL_TOPOLOGY,
    bfs_distances,
    generate_topology,
    grid_candidate_edges,
    parse_cell_key,
)


def undirected_edges(topology) -> set[tuple[str, str]]:
    return {
        tuple(sorted((cell, neighbor)))
        for cell in topology.cells()
        for neighbor in topology.neighbors(cell)
    }


def test_grid_has_49_cells_and_84_candidate_edges() -> None:
    topology = generate_topology(1)
    assert len(topology.cells()) == 49
    assert len(grid_candidate_edges()) == 84


@pytest.mark.parametrize("seed", range(10))
def test_generated_topology_is_connected_symmetric_orthogonal_and_sorted(seed: int) -> None:
    topology = generate_topology(seed)
    assert len(bfs_distances(topology, "cell_0_0")) == 49
    assert len(undirected_edges(topology)) >= 48

    for cell in topology.cells():
        neighbors = topology.neighbors(cell)
        assert neighbors == sorted(neighbors)
        x, y = parse_cell_key(cell)
        for neighbor in neighbors:
            nx, ny = parse_cell_key(neighbor)
            assert abs(x - nx) + abs(y - ny) == 1
            assert topology.is_adjacent(neighbor, cell)


def test_zero_extra_probability_produces_exact_spanning_tree() -> None:
    topology = generate_topology(1234, extra_edge_probability=0)
    assert len(undirected_edges(topology)) == 48


def test_full_extra_probability_restores_every_candidate_edge() -> None:
    topology = generate_topology(1234, extra_edge_probability=1)
    assert len(undirected_edges(topology)) == 84


def test_generation_is_deterministic_but_seed_sensitive() -> None:
    assert generate_topology(100).to_dict() == generate_topology(100).to_dict()
    assert generate_topology(100).to_dict() != generate_topology(101).to_dict()


def test_topology_accessors_return_defensive_copies() -> None:
    topology = generate_topology(5)
    neighbors = topology.neighbors("cell_0_0")
    serialized = topology.to_dict()
    neighbors.clear()
    serialized["cell_0_0"].clear()

    assert topology.neighbors("cell_0_0")


def test_minimal_topology_remains_compatible_with_tracer_bullet() -> None:
    assert MINIMAL_TOPOLOGY.is_adjacent("cell_0_0", "cell_0_1")
    assert MINIMAL_TOPOLOGY.to_dict() == {
        "cell_0_0": ["cell_0_1"],
        "cell_0_1": ["cell_0_0"],
    }


@pytest.mark.parametrize(
    ("width", "height", "probability"),
    [(0, 7, 0.2), (7, 0, 0.2), (True, 7, 0.2), (7, 7, -0.1), (7, 7, 1.1)],
)
def test_invalid_generation_configuration_is_rejected(
    width: int, height: int, probability: float
) -> None:
    with pytest.raises(ValueError):
        generate_topology(
            1, width=width, height=height, extra_edge_probability=probability
        )
