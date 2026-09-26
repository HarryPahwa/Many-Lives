"""Deterministic grid topology generation (TDD §14.1)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from app.domain.rng import topology_rng


def format_cell_key(x: int, y: int) -> str:
    return f"cell_{x}_{y}"


def parse_cell_key(cell_key: str) -> tuple[int, int]:
    parts = cell_key.split("_")
    if len(parts) != 3 or parts[0] != "cell":
        raise ValueError(f"Invalid cell key: {cell_key}")
    try:
        return int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise ValueError(f"Invalid cell key: {cell_key}") from exc


@dataclass
class Topology:
    """Undirected adjacency-list graph with stable, defensive serialization."""

    adjacency: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.adjacency = {
            cell: sorted(set(neighbors)) for cell, neighbors in sorted(self.adjacency.items())
        }

    def cells(self) -> list[str]:
        return list(self.adjacency)

    def neighbors(self, cell_id: str) -> list[str]:
        return list(self.adjacency.get(cell_id, ()))

    def is_adjacent(self, a: str, b: str) -> bool:
        return b in self.adjacency.get(a, ())

    def to_dict(self) -> dict[str, list[str]]:
        return {cell: list(neighbors) for cell, neighbors in self.adjacency.items()}


class _UnionFind:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}
        self.rank = dict.fromkeys(values, 0)

    def find(self, value: str) -> str:
        root = value
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[value] != value:
            value, self.parent[value] = self.parent[value], root
        return root

    def union(self, left: str, right: str) -> bool:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return False
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1
        return True


def grid_candidate_edges(*, width: int = 7, height: int = 7) -> list[tuple[str, str]]:
    """Enumerate each undirected orthogonal grid edge exactly once."""
    _validate_dimensions(width, height)
    edges: list[tuple[str, str]] = []
    for x in range(width):
        for y in range(height):
            cell = format_cell_key(x, y)
            if x + 1 < width:
                edges.append((cell, format_cell_key(x + 1, y)))
            if y + 1 < height:
                edges.append((cell, format_cell_key(x, y + 1)))
    return edges


def generate_topology(
    seed: int,
    *,
    width: int = 7,
    height: int = 7,
    extra_edge_probability: float = 0.2,
) -> Topology:
    """Generate a connected topology using randomized Kruskal plus extra edges."""
    _validate_dimensions(width, height)
    if isinstance(extra_edge_probability, bool) or not 0 <= extra_edge_probability <= 1:
        raise ValueError("extra_edge_probability must be between 0 and 1")

    cells = [format_cell_key(x, y) for x in range(width) for y in range(height)]
    adjacency: dict[str, list[str]] = {cell: [] for cell in cells}
    if len(cells) == 1:
        return Topology(adjacency)

    rng = topology_rng(seed)
    candidates = grid_candidate_edges(width=width, height=height)
    rng.shuffle(candidates)
    components = _UnionFind(cells)
    tree_edges: list[tuple[str, str]] = []
    remaining_edges: list[tuple[str, str]] = []

    for edge in candidates:
        if components.union(*edge):
            tree_edges.append(edge)
        else:
            remaining_edges.append(edge)

    if len(tree_edges) != len(cells) - 1:
        raise RuntimeError("Grid candidate graph unexpectedly failed to connect")

    selected_edges = list(tree_edges)
    selected_edges.extend(
        edge for edge in remaining_edges if rng.random() < extra_edge_probability
    )
    for left, right in selected_edges:
        adjacency[left].append(right)
        adjacency[right].append(left)
    return Topology(adjacency)


def bfs_distances(topology: Topology, start: str) -> dict[str, int]:
    """Return shortest-path distances from start to every reachable cell."""
    if start not in topology.adjacency:
        raise ValueError(f"Unknown start cell: {start}")
    distances = {start: 0}
    queue = deque([start])
    while queue:
        cell = queue.popleft()
        for neighbor in topology.neighbors(cell):
            if neighbor not in distances:
                distances[neighbor] = distances[cell] + 1
                queue.append(neighbor)
    return distances


def is_boundary_cell(cell_key: str, *, width: int = 7, height: int = 7) -> bool:
    _validate_dimensions(width, height)
    x, y = parse_cell_key(cell_key)
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError(f"Cell key outside configured grid: {cell_key}")
    return x in (0, width - 1) or y in (0, height - 1)


def _validate_dimensions(width: int, height: int) -> None:
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width < 1
        or height < 1
    ):
        raise ValueError("width and height must be positive integers")


MINIMAL_TOPOLOGY = Topology(
    adjacency={
        "cell_0_0": ["cell_0_1"],
        "cell_0_1": ["cell_0_0"],
    }
)
