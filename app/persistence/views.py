"""Immutable world snapshots loaded at the persistence boundary (TDD §13.1)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


FrozenValue = Any


def freeze(value: Any) -> FrozenValue:
    """Recursively copy mutable BSON-compatible data into immutable containers."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    """Recursively copy an immutable world-view value into ordinary containers."""
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    if isinstance(value, frozenset):
        return {thaw(item) for item in value}
    return value


@dataclass(frozen=True)
class WorldView:
    campaign: Mapping[str, Any]
    player: Mapping[str, Any]
    current_cell: Mapping[str, Any]
    destination_cell: Mapping[str, Any] | None
    characters: tuple[Mapping[str, Any], ...]
    items: tuple[Mapping[str, Any], ...]
    container_items: tuple[Mapping[str, Any], ...]
    config: Mapping[str, Any]
    owned_items: tuple[Mapping[str, Any], ...] = ()
