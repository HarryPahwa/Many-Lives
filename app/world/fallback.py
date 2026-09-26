"""Deterministic fallback room (TDD §14.8).

Built from cell RNG + small per-archetype name tables when the room dresser
fails after retries. MUST honor all reservations and keep boss/key
reachability. generation_source = FALLBACK.
Scaffold only.
"""
