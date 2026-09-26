from app.domain.checks import adjust_disposition, resolve_check
from app.domain.rng import TurnRng
from app.domain.types import CheckKind, DispositionState


def actor():
    return {"character": {"level": 2, "skill": 3, "attack": 6, "speed": 4}}


def target():
    return {"character": {"level": 4, "speed": 5, "alerted": False}}


def test_check_is_deterministic_and_clamps_approach():
    first = resolve_check(CheckKind.PERSUADE, actor(), rng=TurnRng(9, 2),
                          disposition=DispositionState.WARY, approach_modifier=99)
    second = resolve_check(CheckKind.PERSUADE, actor(), rng=TurnRng(9, 2),
                           disposition=DispositionState.WARY, approach_modifier=99)
    assert first == second
    assert first.approach_modifier == 2
    assert first.relationship_modifier == -2
    assert first.dc == 12
    assert first.total == first.roll + 3 - 2 + 2


def test_dc_and_stat_tables():
    intimidate = resolve_check(CheckKind.INTIMIDATE, actor(), target=target(),
                               rng=TurnRng(1, 1))
    steal = resolve_check(CheckKind.STEAL, actor(), target=target(), rng=TurnRng(1, 1))
    skill = resolve_check(CheckKind.SKILL, actor(), suggested_difficulty=99,
                          rng=TurnRng(1, 1))
    assert intimidate.stat_modifier == 3 and intimidate.dc == 14
    assert steal.stat_modifier == 5 and steal.dc == 15
    assert skill.dc == 18


def test_disposition_hysteresis_and_hostile_positive_halving():
    hostile = adjust_disposition(DispositionState.NEUTRAL, 0, -60)
    assert hostile.state_after == DispositionState.HOSTILE
    still_hostile = adjust_disposition(DispositionState.HOSTILE, -50, 30)
    assert still_hostile.applied_delta == 15
    assert still_hostile.state_after == DispositionState.HOSTILE
    recovered = adjust_disposition(DispositionState.HOSTILE, -12, 5)
    assert recovered.trust_after == -10
    assert recovered.state_after == DispositionState.WARY
