"""Campaign routes (TDD §17.2): create, list, get, resume, map, player.

Scaffold: empty router; endpoints wired later.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])
