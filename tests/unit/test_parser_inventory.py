import pytest

from app.domain.parser import parse_fast_path
from app.domain.types import ActionType


@pytest.mark.parametrize(
    ("command", "action", "query"),
    [
        ("take brass key", ActionType.TAKE_ITEM, "brass key"),
        ("pick up mana potion", ActionType.TAKE_ITEM, "mana potion"),
        ("drop torch", ActionType.DROP_ITEM, "torch"),
        ("wield sword", ActionType.EQUIP, "sword"),
        ("remove mail", ActionType.UNEQUIP, "mail"),
        ("drink potion", ActionType.USE_ITEM, "potion"),
        ("attack cave troll", ActionType.ATTACK, "cave troll"),
        ("interact boss door", ActionType.INTERACT, "boss door"),
        ("search rubble", ActionType.SEARCH, "rubble"),
        ("talk to keeper", ActionType.TALK, "keeper"),
        ("persuade keeper", ActionType.PERSUADE, "keeper"),
        ("deceive keeper", ActionType.DECEIVE, "keeper"),
        ("intimidate keeper", ActionType.INTIMIDATE, "keeper"),
    ],
)
def test_inventory_fast_paths(command, action, query):
    intent = parse_fast_path(command, "player")
    assert intent.action_type == action
    assert intent.params == {"query": query}


def test_flee_fast_path_uses_direction():
    intent = parse_fast_path("flee west", "player")
    assert intent.action_type == ActionType.FLEE
    assert intent.params == {"direction": "WEST"}


def test_steal_fast_path_names_item_and_owner():
    intent = parse_fast_path("steal brass key from keeper", "player")
    assert intent.action_type == ActionType.STEAL
    assert intent.params == {"query": "brass key", "target_query": "keeper"}
