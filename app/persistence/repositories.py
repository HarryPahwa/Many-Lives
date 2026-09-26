"""Repositories (TDD §9.9).

Campaign-scoped reads and the atomic commit_turn / commit_room transactions
with optimistic version checks and idempotency guards. Translates
AppliedEffects to update operations; never decides game outcomes.
Scaffold only.
"""
