"""Pure uncertain checks and disposition mechanics (TDD §4.7, §13.9)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.domain.rng import RollRecord, TurnRng
from app.domain.types import CheckKind, DispositionState

DISPOSITION_MODIFIERS = {DispositionState.FRIENDLY: 2, DispositionState.NEUTRAL: 0,
                         DispositionState.WARY: -2, DispositionState.HOSTILE: -5}


@dataclass(frozen=True)
class CheckOutcome:
    kind: CheckKind
    roll: int
    stat_modifier: int
    relationship_modifier: int
    approach_modifier: int
    total: int
    dc: int
    success: bool
    rolls: tuple[RollRecord, ...]


@dataclass(frozen=True)
class DispositionOutcome:
    state_before: DispositionState
    state_after: DispositionState
    trust_before: int
    trust_after: int
    applied_delta: int

    @property
    def state_changed(self) -> bool:
        return self.state_before != self.state_after


def resolve_check(kind: CheckKind, player: Mapping[str, Any], *, rng: TurnRng,
                  target: Mapping[str, Any] | None = None,
                  disposition: DispositionState = DispositionState.NEUTRAL,
                  approach_modifier: int = 0,
                  suggested_difficulty: int | None = None) -> CheckOutcome:
    stats = player["character"]
    approach = max(-2, min(2, int(approach_modifier)))
    relationship = DISPOSITION_MODIFIERS[disposition]
    if kind in {CheckKind.PERSUADE, CheckKind.DECEIVE, CheckKind.SEARCH, CheckKind.SKILL}:
        stat = int(stats["skill"])
    elif kind == CheckKind.INTIMIDATE:
        stat = max(int(stats["skill"]), int(stats["attack"]) // 2)
    elif kind == CheckKind.STEAL:
        stat = int(stats["skill"]) + int(stats["speed"]) // 2
    else:  # pragma: no cover
        raise ValueError(f"Unsupported check kind: {kind}")
    if kind == CheckKind.PERSUADE:
        dc = 12
    elif kind == CheckKind.DECEIVE:
        dc = 13
    elif kind == CheckKind.INTIMIDATE:
        if target is None:
            raise ValueError("Intimidate requires a target")
        dc = 12 + max(0, int(target["character"]["level"]) - int(stats["level"]))
    elif kind == CheckKind.STEAL:
        if target is None:
            raise ValueError("Steal requires a target")
        dc = 10 + int(target["character"]["speed"])
        relationship = 0
        if target["character"].get("alerted"):
            relationship -= 2
    elif kind == CheckKind.SEARCH:
        dc, relationship = int(suggested_difficulty or 12), 0
    else:
        dc, relationship = max(10, min(18, int(suggested_difficulty or 10))), 0
    before = len(rng.records)
    roll = rng.roll("check", 20)
    total = roll + stat + relationship + approach
    return CheckOutcome(kind, roll, stat, relationship, approach, total, dc,
                        total >= dc, rng.records[before:])


def adjust_disposition(state: DispositionState, trust: int, delta: int) -> DispositionOutcome:
    applied = int(delta)
    if state == DispositionState.HOSTILE and applied > 0:
        applied //= 2
    after_trust = max(-100, min(100, int(trust) + applied))
    after_state = state
    if after_trust <= -50:
        after_state = DispositionState.HOSTILE
    elif state == DispositionState.HOSTILE and after_trust >= -10:
        after_state = DispositionState.WARY
    elif state in {DispositionState.WARY, DispositionState.NEUTRAL} and after_trust >= 40:
        after_state = DispositionState.FRIENDLY
    elif state == DispositionState.FRIENDLY and after_trust < 20:
        after_state = DispositionState.NEUTRAL
    elif state == DispositionState.NEUTRAL and after_trust < -20:
        after_state = DispositionState.WARY
    elif state == DispositionState.WARY and after_trust >= 0:
        after_state = DispositionState.NEUTRAL
    return DispositionOutcome(state, after_state, int(trust), after_trust, applied)
