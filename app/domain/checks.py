"""Uncertain/social checks and disposition model (TDD §4.7, §13.9).

d20 + stat_mod + relationship_env_mod + approach_mod >= DC.
Disposition trust in [-100, 100] with hysteresis so hostility is hard to
reverse; trust deltas per action; positive deltas halved while HOSTILE.
Scaffold only.
"""
