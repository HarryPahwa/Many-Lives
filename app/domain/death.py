"""Death, respawn, and XP (TDD §4.5, §4.8, §13.8).

On death: drop one item (carried slot first, else equipped), place on death
cell floor guarded by remaining hostiles, award non-zero death XP, respawn at
spawn with full HP/MP, reset since-death counters. Kill XP by level diff.
Scaffold only.
"""
