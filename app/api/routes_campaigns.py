"""Campaign routes (TDD §17.2): create, list, get, resume, map, player.

Every handler is campaign-scoped (§5.10, §22): the campaign_id from the path
is passed to every seam call, and an unknown campaign is a 404 before any
other work happens.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.schemas import (
    CampaignSummary,
    CreateCampaignRequest,
    CreateCampaignResponse,
    HistoryStats,
    MapResponse,
    PlayerSheet,
    ResumeRequest,
    ResumeResult,
    TurnRequest,
)
from app.services.stubs import get_engine, get_harness
from app.services.turn_orchestrator import CampaignNotFound, get_orchestrator

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


def _require(campaign_id: str) -> CampaignSummary:
    summary = get_engine().get_campaign(campaign_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Unknown campaign.")
    return summary


@router.post("", response_model=CreateCampaignResponse, status_code=201)
def create_campaign(body: CreateCampaignRequest) -> CreateCampaignResponse:
    """§17.2: returns a CampaignSummary plus the initial TurnResult for spawn."""
    summary = get_engine().create_campaign(body.player_name, body.seed)
    # The spawn description is produced as a LOOK turn so the client's first
    # render goes through exactly the same path as every later turn.
    initial = get_orchestrator().take_turn(
        summary.campaign_id,
        TurnRequest(
            turn_id=f"spawn-{summary.campaign_id}",
            player_id=summary.player_id,
            input="look",
        ),
    )
    refreshed = get_engine().get_campaign(summary.campaign_id) or summary
    return CreateCampaignResponse(campaign=refreshed, initial=initial)


@router.get("", response_model=list[CampaignSummary])
def list_campaigns() -> list[CampaignSummary]:
    return get_engine().list_campaigns()


@router.get("/{campaign_id}", response_model=CampaignSummary)
def get_campaign(campaign_id: str) -> CampaignSummary:
    return _require(campaign_id)


@router.post("/{campaign_id}/resume", response_model=ResumeResult)
def resume_campaign(campaign_id: str, body: ResumeRequest) -> ResumeResult:
    try:
        return get_orchestrator().resume(campaign_id, body.player_id)
    except CampaignNotFound:
        raise HTTPException(status_code=404, detail="Unknown campaign.") from None


@router.get("/{campaign_id}/map", response_model=MapResponse)
def get_map(campaign_id: str, player_id: str = Query(default="player_1")) -> MapResponse:
    _require(campaign_id)
    return get_engine().build_map(campaign_id, player_id)


@router.get("/{campaign_id}/player", response_model=PlayerSheet)
def get_player(
    campaign_id: str, player_id: str = Query(default="player_1")
) -> PlayerSheet:
    _require(campaign_id)
    return get_engine().player_sheet(campaign_id, player_id)


@router.get("/{campaign_id}/history", response_model=HistoryStats)
def get_history(campaign_id: str) -> HistoryStats:
    """How much history this campaign has stored, against the context budget.

    `history_stats` is an optional engine capability (§16.4), so an engine
    without it reports `supported: false` instead of a 500: the claim this
    serves is evidence, and evidence that cannot be gathered should say so.
    """
    _require(campaign_id)
    budget = get_harness().context_budget()
    stats = getattr(get_engine(), "history_stats", None)
    if not callable(stats):
        return HistoryStats(
            campaign_id=campaign_id, supported=False, budget_tokens=budget
        )
    return HistoryStats(
        campaign_id=campaign_id, budget_tokens=budget, **stats(campaign_id)
    )
