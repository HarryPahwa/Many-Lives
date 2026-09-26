"""Agentic Dungeon Harness — application package.

Layered per TDD §6.2. Dependency rule: `domain` imports nothing from
`harness`, `persistence`, or `api`; `harness` may import `domain` types;
`services` composes all layers.
"""
