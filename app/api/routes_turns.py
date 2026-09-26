"""Turn routes (TDD §17.2): POST /api/campaigns/{id}/turns.

The request body carries a client-generated `turn_id` (§18); resending the
same id is safe and returns the stored result without applying anything again
(§7.1, §9.9).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.schemas import TurnRequest, TurnResult
from app.services.stubs import ConcurrencyConflict
from app.services.turn_orchestrator import (
    CampaignNotActive,
    CampaignNotFound,
    get_orchestrator,
)

router = APIRouter(prefix="/api/campaigns", tags=["turns"])


@router.post("/{campaign_id}/turns", response_model=TurnResult)
def take_turn(campaign_id: str, body: TurnRequest) -> TurnResult:
    try:
        return get_orchestrator().take_turn(campaign_id, body)
    except CampaignNotFound:
        raise HTTPException(status_code=404, detail="Unknown campaign.") from None
    except CampaignNotActive:
        raise HTTPException(
            status_code=409, detail="This campaign is not accepting turns."
        ) from None
    except ConcurrencyConflict:
        raise HTTPException(
            status_code=409, detail="The campaign changed during this turn; retry."
        ) from None
