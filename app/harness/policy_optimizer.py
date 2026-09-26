"""Policy optimizer (P0.5) (TDD §16.3).

Detects the dominant failure category, proposes ONE allowed mutation (closed
set), re-runs probes, and promotes or rolls back within a token budget.
Automatic end-to-end run is the Statement One claim.
Scaffold only.
"""
