"""Index creation (TDD §9.2-§9.8, §12.5).

B-tree indexes for all collections plus the single Atlas Vector Search index
on `memories` (campaign_id/entity_ids/cell_id/memory_type filter fields).
Poll list_search_indexes until queryable.
Scaffold only.
"""
