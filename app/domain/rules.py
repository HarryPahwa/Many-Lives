"""Rules engine — validation pipeline and action handlers (TDD §13.1, §13.3, §13.6).

resolve(action, view, rng) -> Resolution. Recomputes every precondition,
enforces the adjudicator effect allowlist, rolls RNG, resolves the action,
then runs the environment-response phase. Produces AppliedEffects + EventDrafts.
Never performs I/O.
Scaffold only.
"""
