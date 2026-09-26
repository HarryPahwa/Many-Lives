"""Fast-path parser (TDD §13.2).

Maps unambiguous commands to ActionIntent (move/attack/take/equip/use/...).
Name resolution: exact -> prefix -> substring against visible entities,
features, carried items. Zero matches -> fall through to adjudicator;
multiple -> AMBIGUOUS_TARGET.
Scaffold only.
"""
