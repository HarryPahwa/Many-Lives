"""Synthetic long-history script (P1) (TDD §16.4).

Builds a probe campaign with ~10,000 events and ~1,000 memories (float32
BinData, batched inserts), then runs P07 to show stored history growing while
per-call context stays within budget. Run before 15:00; back off on throttle.
Scaffold only.
"""
