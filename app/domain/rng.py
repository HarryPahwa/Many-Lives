"""Deterministic named RNG streams (TDD §13.5)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import random
from typing import TypeVar


T = TypeVar("T")
TURN_PURPOSES = frozenset({"combat", "check", "drop", "env"})


@dataclass(frozen=True)
class RollRecord:
    """One recorded authoritative random result."""

    purpose: str
    operation: str
    value: int | float | str | bool
    sides: int | None = None


class TurnRng:
    """Independent deterministic random streams for one campaign turn."""

    def __init__(
        self,
        campaign_seed: int,
        turn_sequence: int,
        *,
        allowed_purposes: frozenset[str] = TURN_PURPOSES,
    ) -> None:
        if turn_sequence < 0:
            raise ValueError("turn_sequence cannot be negative")
        if not allowed_purposes:
            raise ValueError("allowed_purposes cannot be empty")
        self._campaign_seed = campaign_seed
        self._turn_sequence = turn_sequence
        self._allowed_purposes = allowed_purposes
        self._streams: dict[str, random.Random] = {}
        self._records: list[RollRecord] = []

    @property
    def records(self) -> tuple[RollRecord, ...]:
        return tuple(self._records)

    def _stream(self, purpose: str) -> random.Random:
        if purpose not in self._allowed_purposes:
            raise ValueError(f"Unknown RNG purpose: {purpose}")
        if purpose not in self._streams:
            seed = f"{self._campaign_seed}:{self._turn_sequence}:{purpose}"
            self._streams[purpose] = random.Random(seed)
        return self._streams[purpose]

    def roll(self, purpose: str, sides: int) -> int:
        """Roll one die in the inclusive range 1..sides and record it."""
        if isinstance(sides, bool) or not isinstance(sides, int) or sides < 1:
            raise ValueError("sides must be a positive integer")
        value = self._stream(purpose).randint(1, sides)
        self._records.append(RollRecord(purpose, "roll", value, sides))
        return value

    def randint(self, purpose: str, minimum: int, maximum: int) -> int:
        value = self._stream(purpose).randint(minimum, maximum)
        self._records.append(RollRecord(purpose, "randint", value))
        return value

    def choice(self, purpose: str, values: Sequence[T]) -> T:
        if not values:
            raise ValueError("cannot choose from an empty sequence")
        index = self._stream(purpose).randrange(len(values))
        self._records.append(RollRecord(purpose, "choice_index", index))
        return values[index]

    def shuffled(self, purpose: str, values: Sequence[T]) -> list[T]:
        result = list(values)
        self._stream(purpose).shuffle(result)
        self._records.append(RollRecord(purpose, "shuffle", len(result)))
        return result

    def chance(self, purpose: str, probability: float) -> bool:
        if isinstance(probability, bool) or not 0 <= probability <= 1:
            raise ValueError("probability must be between 0 and 1")
        value = self._stream(purpose).random() < probability
        self._records.append(RollRecord(purpose, "chance", value))
        return value


def world_rng(seed: int, stream: str) -> random.Random:
    """Return a deterministic, isolated world-generation stream."""
    if stream not in {"topology", "placement"} and not (
        stream.startswith("cell:") and stream.endswith(":plan")
    ):
        raise ValueError(f"Unknown world RNG stream: {stream}")
    return random.Random(f"{seed}:{stream}")


def topology_rng(seed: int) -> random.Random:
    return world_rng(seed, "topology")


def placement_rng(seed: int) -> random.Random:
    return world_rng(seed, "placement")


def cell_plan_rng(seed: int, cell_key: str) -> random.Random:
    if not cell_key:
        raise ValueError("cell_key cannot be empty")
    return world_rng(seed, f"cell:{cell_key}:plan")
