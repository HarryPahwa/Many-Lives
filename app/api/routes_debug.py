"""Debug/inspector routes (TDD §17.2): context manifest per turn.

Enabled only when DEBUG_ENDPOINTS=true (TDD §22).
Scaffold: empty router; endpoints wired later.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/campaigns", tags=["debug"])
