"""Narration verifier (TDD §16.1).

Deterministic: compares each claim against the post-commit snapshot, flags
contradictions, invented entities, and absent-entity mentions. Records counts
in turns.verification. One regeneration in live play; never in probes.
Scaffold only.
"""
