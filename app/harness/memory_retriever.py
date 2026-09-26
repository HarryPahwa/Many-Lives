"""Memory retriever (TDD §12.5).

$vectorSearch on `memories` with mandatory campaign_id pre-filter plus
per-policy entity/cell/type filters. Dedupes against recent_events. On index
unavailability, proceed without memories and record VECTOR_UNAVAILABLE.
Scaffold only.
"""
