"""Turn routes (TDD §17.2): POST /api/campaigns/{id}/turns.

Scaffold: empty router; endpoints wired later.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/campaigns", tags=["turns"])
