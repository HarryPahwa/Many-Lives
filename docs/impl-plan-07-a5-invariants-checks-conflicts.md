# Implementation Plan 07 — A5 Invariants, Checks, and Conflict Alignment

**Owner:** Developer A  
**Status:** implemented and verified (2026-09-26)  
**Depends on:** Plans 01–06

## Goal

Implement INV-01 through INV-15 as structured pure checks, persist post-commit
results without rolling back canonical state, provide a full campaign sweep,
unify optimistic-conflict signaling with the turn orchestrator, and complete
the P0 check/disposition mechanics while the core remains green.

## Design decisions

- Invariant checks return stable IDs and document references, never booleans
  without diagnostic context.
- INV-07 and INV-08 use immutable persisted baselines (`origin_cell_id` and a
  static-environment digest); current state alone cannot prove history.
- INV-13 combines campaign-ID validation of all sweep inputs with repository
  query-scope tests. It is not inferred from canonical state alone.
- Checks run after the commit transaction. Failures are written to
  `turns.invariants` and do not roll back committed effects.
- Persistence and orchestration share `domain.errors.ConcurrencyConflict`;
  Developer C's existing one-retry lifecycle remains authoritative.
- Social rolls use the existing deterministic `check` stream and record the
  complete roll/DC/modifier outcome.

## Completed steps

1. ✅ Structured invariant report and immutable baselines.
2. ✅ Pure INV-01…15 full campaign checker.
3. ✅ Post-commit invariant recording and repository sweep API.
4. ✅ Shared concurrency-conflict type across A and C seams.
5. ✅ Pure check/DC/modifier and disposition-hysteresis mechanics.
6. ✅ SEARCH/TALK/PERSUADE/DECEIVE/INTIMIDATE/STEAL fast paths and resolution.
7. ✅ Fifty-turn invariant sweep and regression verification.

## Verification

```bash
pytest tests/unit/test_invariants.py tests/unit/test_checks.py -v
pytest tests/integration/test_invariant_sweep.py -v
pytest tests/unit tests/integration/test_campaign_core.py \
  tests/integration/test_room_generation.py \
  tests/integration/test_combat_persistence.py \
  tests/integration/test_turn_idempotency.py \
  tests/integration/test_invariant_sweep.py -q
python -m compileall -q app tests
git diff --check
```

No Atlas credentials are required. Browser E2E and the local FastAPI
TestClient dependency issue remain separate environment checks.
