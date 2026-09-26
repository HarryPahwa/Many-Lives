"""Invariant checker (TDD §9.10, INV-01..INV-15).

Runs after each commit on touched entities/cells and fully in integration
tests. Failures are logged to turns.invariants and counted as a hard metric;
they never roll back a committed turn.
Scaffold only.
"""
