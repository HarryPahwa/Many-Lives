"""Contract checks for the TDD balance defaults."""

from pathlib import Path

import yaml


BALANCE_PATH = Path(__file__).parents[2] / "config" / "balance.yaml"


def load_balance() -> dict:
    with BALANCE_PATH.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def test_balance_has_every_required_section() -> None:
    balance = load_balance()
    assert set(balance) == {
        "world",
        "player",
        "level_up",
        "experience",
        "combat",
        "checks",
        "inventory",
        "disposition",
        "features",
        "generation",
        "context",
    }


def test_world_and_player_defaults_match_tdd() -> None:
    balance = load_balance()
    world = balance["world"]
    player = balance["player"]

    assert (world["width"], world["height"]) == (7, 7)
    assert world["keys_required"] == 3
    assert world["key_reservations"] == 6
    assert world["min_spawn_boss_distance"] == 5
    assert world["extra_edge_probability"] == 0.2
    assert player["carried_slots"] == 6
    assert player["dodge_pct_cap"] == 40
    assert (player["max_hp"], player["max_mp"]) == (20, 6)


def test_generation_weight_tables_total_one_hundred() -> None:
    generation = load_balance()["generation"]
    assert sum(generation["archetype_weights"].values()) == 100
    assert sum(generation["key_carrier_weights"].values()) == 100
    assert sum(generation["loot_weights"].values()) == 100


def test_generation_and_context_defaults_match_tdd() -> None:
    balance = load_balance()
    generation = balance["generation"]

    assert generation["room_feature_range"] == [2, 5]
    assert generation["dresser_retries"] == 2
    assert generation["generation_claim_timeout_seconds"] == 60
    assert generation["character_stats"]["boss"]["hp"] == 60
    assert balance["features"]["max_per_cell"] == 12
    assert balance["context"]["max_context_tokens"] == 3000


def test_mechanical_caps_and_resource_values_match_tdd() -> None:
    balance = load_balance()
    assert balance["combat"]["damage_variance"] == [-1, 0, 1]
    assert balance["combat"]["spell"] == {"mp_cost": 3, "damage": 6}
    assert balance["checks"]["approach_modifier"] == {"minimum": -2, "maximum": 2}
    assert balance["checks"]["creative_dc"] == {"minimum": 10, "maximum": 18}
    assert balance["inventory"]["max_stack"] == 3
    assert balance["inventory"]["mana_potion_restore"] == 4
    assert balance["experience"]["death"]["total_pct_cap"] == 40


def test_disposition_contains_every_engine_delta() -> None:
    deltas = load_balance()["disposition"]["deltas"]
    assert deltas == {
        "attacked": -60,
        "theft_failed": -40,
        "theft_succeeded": -20,
        "deception_failed": -20,
        "intimidation_succeeded": -15,
        "intimidation_failed": -25,
        "persuasion_failed": -5,
        "persuasion_succeeded": 10,
        "item_gift": 15,
        "quest_completed": 30,
        "adjudicator_worsen": -15,
        "adjudicator_improve": 5,
    }
