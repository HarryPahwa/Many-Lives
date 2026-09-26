"""Narrator (TDD §10.6).

Read-only model call over committed events + current snapshot. Returns prose +
machine-checkable claims. Never mutates state, never invents entities, only
reveals allowed facts. Template fallback when the model fails.
Scaffold only.
"""
