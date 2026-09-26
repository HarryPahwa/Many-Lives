"""Fast-path command parser (TDD §13.1).

Deterministic string matching for standard game commands (0ms, skips LLM).
"""

from app.domain.types import ActionIntent, ActionType


DIRECTION_MAP = {
    "north": "NORTH",
    "n": "NORTH",
    "south": "SOUTH",
    "s": "SOUTH",
    "east": "EAST",
    "e": "EAST",
    "west": "WEST",
    "w": "WEST",
}

ITEM_COMMANDS = {
    "take": ActionType.TAKE_ITEM,
    "get": ActionType.TAKE_ITEM,
    "loot": ActionType.TAKE_ITEM,
    "drop": ActionType.DROP_ITEM,
    "equip": ActionType.EQUIP,
    "wield": ActionType.EQUIP,
    "wear": ActionType.EQUIP,
    "unequip": ActionType.UNEQUIP,
    "remove": ActionType.UNEQUIP,
    "use": ActionType.USE_ITEM,
    "drink": ActionType.USE_ITEM,
}

def parse_fast_path(text: str, actor_id: str) -> ActionIntent | None:
    """Attempt to parse text input into a deterministic ActionIntent.
    
    Returns None if input is free-form and requires LLM adjudication.
    """
    clean = text.strip().lower()
    if not clean:
        return None

    # Directions / Move
    if clean in DIRECTION_MAP:
        return ActionIntent(
            action_type=ActionType.MOVE,
            actor_id=actor_id,
            params={"direction": DIRECTION_MAP[clean]},
        )
    
    parts = clean.split(maxsplit=1)
    cmd = parts[0]
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("go", "move") and arg in DIRECTION_MAP:
        return ActionIntent(
            action_type=ActionType.MOVE,
            actor_id=actor_id,
            params={"direction": DIRECTION_MAP[arg]},
        )

    if cmd == "flee" and arg in DIRECTION_MAP:
        return ActionIntent(
            action_type=ActionType.FLEE,
            actor_id=actor_id,
            params={"direction": DIRECTION_MAP[arg]},
        )

    if cmd in {"attack", "hit", "strike"} and arg:
        return ActionIntent(
            action_type=ActionType.ATTACK,
            actor_id=actor_id,
            params={"query": arg},
        )

    if cmd in {"interact", "open"} and arg:
        return ActionIntent(
            action_type=ActionType.INTERACT,
            actor_id=actor_id,
            params={"query": arg},
        )

    if cmd == "look":
        return ActionIntent(
            action_type=ActionType.LOOK,
            actor_id=actor_id,
            targets=[arg] if arg else [],
        )

    if cmd == "wait":
        return ActionIntent(
            action_type=ActionType.WAIT,
            actor_id=actor_id,
        )

    if clean.startswith("pick up "):
        arg = clean.removeprefix("pick up ").strip()
        if arg:
            return ActionIntent(
                action_type=ActionType.TAKE_ITEM,
                actor_id=actor_id,
                params={"query": arg},
            )

    if cmd in ITEM_COMMANDS and arg:
        return ActionIntent(
            action_type=ITEM_COMMANDS[cmd],
            actor_id=actor_id,
            params={"query": arg},
        )

    return None
