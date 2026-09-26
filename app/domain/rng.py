"""Seeded RNG service (TDD §13.5).

TurnRng wraps random.Random seeded with f"{campaign.seed}:{turn_sequence}:{purpose}".
Purposes: combat, check, drop, env. World-gen streams: topology, placement,
cell:{cell_key}:plan. Every roll is recorded for event payloads.
Scaffold only.
"""
