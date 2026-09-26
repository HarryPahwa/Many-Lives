"""Tests for deterministic named RNG streams (TDD §13.5)."""

import random

import pytest

from app.domain.rng import TurnRng, cell_plan_rng, placement_rng, topology_rng, world_rng


def test_turn_rolls_match_the_exact_string_seed() -> None:
    rng = TurnRng(8675309, 12)
    expected = random.Random("8675309:12:combat")

    assert [rng.roll("combat", 20) for _ in range(5)] == [
        expected.randint(1, 20) for _ in range(5)
    ]


def test_turn_rng_is_repeatable_and_separated_by_purpose_and_turn() -> None:
    first = TurnRng(42, 7)
    repeated = TurnRng(42, 7)
    next_turn = TurnRng(42, 8)

    first_combat = [first.roll("combat", 100) for _ in range(8)]
    assert first_combat == [repeated.roll("combat", 100) for _ in range(8)]
    assert first_combat != [next_turn.roll("combat", 100) for _ in range(8)]

    expected_check = random.Random("42:7:check")
    assert first.roll("check", 20) == expected_check.randint(1, 20)


def test_rolls_are_bounded_and_recorded() -> None:
    rng = TurnRng(1, 1)
    values = [rng.roll("drop", 6) for _ in range(20)]

    assert all(1 <= value <= 6 for value in values)
    assert [record.value for record in rng.records] == values
    assert all(record.purpose == "drop" for record in rng.records)
    assert all(record.sides == 6 for record in rng.records)


def test_choice_shuffle_and_chance_are_repeatable_without_mutating_input() -> None:
    values = [1, 2, 3, 4]
    first = TurnRng(99, 3)
    second = TurnRng(99, 3)

    assert first.choice("env", values) == second.choice("env", values)
    assert first.shuffled("env", values) == second.shuffled("env", values)
    assert first.chance("env", 0.4) == second.chance("env", 0.4)
    assert values == [1, 2, 3, 4]


def test_world_streams_use_exact_tdd_seed_strings() -> None:
    assert topology_rng(123).random() == random.Random("123:topology").random()
    assert placement_rng(123).random() == random.Random("123:placement").random()
    assert cell_plan_rng(123, "cell_4_6").random() == random.Random(
        "123:cell:cell_4_6:plan"
    ).random()


def test_world_streams_are_isolated() -> None:
    topology = topology_rng(55)
    for _ in range(100):
        topology.random()

    assert placement_rng(55).random() == random.Random("55:placement").random()


@pytest.mark.parametrize("purpose", ["damage", "", "Combat"])
def test_unknown_turn_purpose_is_rejected(purpose: str) -> None:
    with pytest.raises(ValueError, match="Unknown RNG purpose"):
        TurnRng(1, 1).roll(purpose, 6)


@pytest.mark.parametrize("stream", ["", "world", "cell:cell_1_1"])
def test_unknown_world_stream_is_rejected(stream: str) -> None:
    with pytest.raises(ValueError, match="Unknown world RNG stream"):
        world_rng(1, stream)


@pytest.mark.parametrize("sides", [0, -1, True, 1.5])
def test_invalid_die_is_rejected(sides: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        TurnRng(1, 1).roll("combat", sides)  # type: ignore[arg-type]
