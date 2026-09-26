"""Turn orchestrator (TDD §7.1) — the integration seam (Developer C).

Runs the fixed turn lifecycle: receive -> parse/adjudicate -> resolve ->
commit -> post-commit (memory + invariants) -> narrate -> respond. Holds the
per-campaign lock; model calls never occur inside the DB transaction.
Scaffold only.
"""
