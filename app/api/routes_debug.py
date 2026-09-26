"""Debug/inspector routes (TDD §17.2): context manifest per turn.

These expose hidden state (proposals, retrieved memory IDs, model latency), so
they are served only when DEBUG_ENDPOINTS=true (TDD §22). With the flag off the
route returns 404 — not 403 — so the demo environment's shape is not
advertised by a disabled deployment.

`turn_id` is optional **[DEF]**: §17.2 does not say what happens without one,
and the inspector panel needs "the turn that just happened", so omitting it
returns the most recent turn for the campaign.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.schemas import (
    ClaimCheck,
    ContextManifest,
    DebugContext,
    ModelCall,
    Verification,
)
from app.config import get_settings
from app.services.stubs import TurnRecord, get_engine

router = APIRouter(prefix="/api/campaigns", tags=["debug"])


@router.get("/{campaign_id}/debug/context", response_model=DebugContext)
def debug_context(
    campaign_id: str, turn_id: str | None = Query(default=None)
) -> DebugContext:
    if not get_settings().debug_endpoints:
        raise HTTPException(status_code=404, detail="Not found.")

    engine = get_engine()
    if engine.get_campaign(campaign_id) is None:
        raise HTTPException(status_code=404, detail="Unknown campaign.")

    record = (
        engine.get_turn(campaign_id, turn_id)
        if turn_id
        else engine.latest_turn(campaign_id)
    )
    if record is None:
        raise HTTPException(status_code=404, detail="No such turn.")
    return _to_debug_context(record)


def _to_debug_context(record: TurnRecord) -> DebugContext:
    narration = record.narration or {}
    claims = [
        ClaimCheck(
            entity_id=str(c.get("entity_id", "")),
            attribute=str(c.get("attribute", "")),
            value=c.get("value"),
            verdict=str(c.get("verdict", "UNCHECKED")),
        )
        for c in narration.get("claims", [])
    ]
    return DebugContext(
        campaign_id=record.campaign_id,
        turn_id=record.turn_id,
        kind=record.kind,
        status=record.status,
        path=record.path,
        action_class=record.action_class,
        reason_code=record.reason_code,
        input=record.input,
        context_manifest=(
            ContextManifest.model_validate(record.context_manifest)
            if record.context_manifest
            else None
        ),
        proposal=record.proposal,
        accepted_effect_types=list(record.accepted_effect_types),
        rejected_effects=list(record.rejected_effects),
        event_ids=list(record.event_ids),
        claims=claims,
        verification=(
            Verification.model_validate(record.verification)
            if record.verification
            else Verification(claims_checked=len(claims))
        ),
        invariants=record.invariants,
        model_calls=[ModelCall.model_validate(c) for c in record.model_calls],
        vector_search_ms=record.vector_search_ms,
        created_at=record.created_at,
        committed_at=record.committed_at,
        narrated_at=record.narrated_at,
    )
