"""Deterministic rules engine (TDD §13 & §14).

Validates preconditions, resolves mechanics, and produces state updates and
immutable event drafts. Pure domain logic: takes domain types (not Mongo
documents) as input.
"""

from dataclasses import dataclass, field
from typing import Any

from app.domain.types import (
    ActionIntent,
    ActionType,
    Event,
    EventType,
    MemoryStatus,
)
from app.world.topology import Topology


@dataclass
class Resolution:
    accepted: bool
    reason: str | None = None
    effects: list[dict[str, Any]] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    state_updates: dict[str, Any] = field(default_factory=dict)
    outcome_summary: str = ""


DIRECTION_OFFSETS = {
    "NORTH": (0, 1),
    "SOUTH": (0, -1),
    "EAST": (1, 0),
    "WEST": (-1, 0),
}


def parse_cell_coords(cell_id: str) -> tuple[int, int]:
    """Parse 'cell_4_6' into (4, 6)."""
    parts = cell_id.split("_")
    return int(parts[1]), int(parts[2])


def format_cell_id(x: int, y: int) -> str:
    """Format (4, 6) into 'cell_4_6'."""
    return f"cell_{x}_{y}"


def resolve_action(
    intent: ActionIntent,
    topology: Topology,
    current_cell_id: str,
    campaign_id: str,
    turn_sequence: int,
    turn_id: str,
) -> Resolution:
    """Resolve an action deterministically against the world state.

    Args:
        intent: The parsed (or adjudicated) action to resolve.
        topology: Cell connectivity graph.
        current_cell_id: The actor's current cell.
        campaign_id: Owning campaign (for event IDs).
        turn_sequence: 1-based turn number.
        turn_id: Client-generated idempotency key.
    """
    actor_id = intent.actor_id

    if intent.action_type == ActionType.MOVE:
        direction = intent.params.get("direction")
        if direction not in DIRECTION_OFFSETS:
            return Resolution(accepted=False, reason=f"Invalid direction '{direction}'")

        cur_x, cur_y = parse_cell_coords(current_cell_id)
        dx, dy = DIRECTION_OFFSETS[direction]
        target_cell_id = format_cell_id(cur_x + dx, cur_y + dy)

        if not topology.is_adjacent(current_cell_id, target_cell_id):
            return Resolution(
                accepted=False,
                reason=f"A wall blocks the way to the {direction.lower()}.",
            )

        event = Event(
            campaign_id=campaign_id,
            event_id=f"evt_{turn_sequence}_0",
            turn_sequence=turn_sequence,
            event_index=0,
            turn_id=turn_id,
            type=EventType.PLAYER_MOVED,
            actor_id=actor_id,
            entity_ids=[actor_id],
            cell_id=target_cell_id,
            payload={
                "from_cell": current_cell_id,
                "to_cell": target_cell_id,
                "direction": direction,
            },
            summary=f"Player moved {direction.lower()} to {target_cell_id}.",
            memory_status=MemoryStatus.NOT_REQUIRED,
        )

        return Resolution(
            accepted=True,
            events=[event],
            state_updates={
                "player_location": {"kind": "CELL", "ref_id": target_cell_id, "slot": None},
                "discovered_cell": target_cell_id,
            },
            outcome_summary=f"Moved {direction.lower()} into {target_cell_id}.",
        )

    if intent.action_type == ActionType.LOOK:
        return Resolution(
            accepted=True,
            outcome_summary=f"Looking around {current_cell_id}.",
        )

    if intent.action_type == ActionType.WAIT:
        return Resolution(
            accepted=True,
            outcome_summary="You wait a moment.",
        )

    return Resolution(accepted=False, reason=f"Unsupported action {intent.action_type}")