"""Grid topology (TDD §14.1).

Symmetric adjacency list of cell connectivity. The tracer bullet hardcodes a
minimal 2-cell topology; the real 7x7 Kruskal spanning-tree generator replaces
``MINIMAL_TOPOLOGY`` later without changing this dataclass's interface.
"""

from dataclasses import dataclass, field


@dataclass
class Topology:
    """Undirected adjacency-list graph of cell connectivity.

    ``adjacency[a]`` lists the cells reachable from ``a``. Symmetric by
    contract (``b in adjacency[a] <=> a in adjacency[b]``).
    """

    adjacency: dict[str, list[str]] = field(default_factory=dict)

    def cells(self) -> list[str]:
        return list(self.adjacency.keys())

    def neighbors(self, cell_id: str) -> list[str]:
        return self.adjacency.get(cell_id, [])

    def is_adjacent(self, a: str, b: str) -> bool:
        return b in self.adjacency.get(a, [])

    def to_dict(self) -> dict[str, list[str]]:
        return self.adjacency


MINIMAL_TOPOLOGY = Topology(
    adjacency={
        "cell_0_0": ["cell_0_1"],
        "cell_0_1": ["cell_0_0"],
    }
)