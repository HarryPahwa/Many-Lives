"""Persistence layer (Developer A) — TDD §9.

Mongo client, campaign-scoped repositories, the commit_turn transaction,
WorldView loaders, and index creation. The only layer that writes canonical
state. Every query is scoped by campaign_id.
"""
