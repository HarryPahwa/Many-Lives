"""Room visual routes (Room Visuals §10).

Every route 404s when ``ENABLE_ROOM_VISUALS`` is false — the same pattern as
`routes_debug.py`, and for the same reason: a disabled deployment should not
advertise its shape.

The module is imported unconditionally and the flag is read per request, so a
test can toggle it without rebuilding the app.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response

from app.api.schemas import RoomVisualStatus
from app.config import get_settings
from app.services import visual_service
from app.services.stubs import get_engine
from app.services.visual_store import get_store, is_valid_asset_id

router = APIRouter(prefix="/api/campaigns", tags=["visuals"])


def _require_enabled() -> None:
    if not get_settings().enable_room_visuals:
        raise HTTPException(status_code=404, detail="Not found.")


def _require_campaign(campaign_id: str) -> None:
    if get_engine().get_campaign(campaign_id) is None:
        raise HTTPException(status_code=404, detail="Unknown campaign.")


@router.get(
    "/{campaign_id}/cells/{cell_id}/visual", response_model=RoomVisualStatus
)
def get_visual_status(campaign_id: str, cell_id: str) -> RoomVisualStatus:
    _require_enabled()
    _require_campaign(campaign_id)

    engine = get_engine()
    record, spec, dirty = visual_service.status_for(engine, campaign_id, cell_id)
    if spec is None:
        # Undiscovered, ungenerated, or not a cell: an image would reveal a room
        # the player has not reached (VIS-06).
        raise HTTPException(status_code=404, detail="No visual for that cell.")

    return RoomVisualStatus.model_validate(
        visual_service.status_payload(campaign_id, cell_id, record, dirty)
    )


@router.post(
    "/{campaign_id}/cells/{cell_id}/visual", response_model=RoomVisualStatus
)
def request_visual(
    campaign_id: str,
    cell_id: str,
    response: Response,
    background: BackgroundTasks,
) -> RoomVisualStatus:
    _require_enabled()
    _require_campaign(campaign_id)

    engine = get_engine()
    record, spec, dirty = visual_service.status_for(engine, campaign_id, cell_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="No visual for that cell.")

    # Nothing to do: the stored image still matches the world (VIS-04).
    if (
        record is not None
        and record.status == visual_service.STATUS_READY
        and not dirty
    ):
        return RoomVisualStatus.model_validate(
            visual_service.status_payload(campaign_id, cell_id, record, dirty)
        )

    claimed = get_store().claim(campaign_id, cell_id)
    if claimed is None:
        # Someone else is already rendering this cell (VIS-05).
        response.status_code = 202
        current = get_store().get(campaign_id, cell_id)
        return RoomVisualStatus.model_validate(
            visual_service.status_payload(campaign_id, cell_id, current, False)
        )

    # The spec captured now is what gets rendered; if the world moves on, the
    # next GET reports dirty again.
    background.add_task(visual_service.render, campaign_id, cell_id, spec)
    response.status_code = 202
    return RoomVisualStatus.model_validate(
        visual_service.status_payload(campaign_id, cell_id, claimed, False)
    )


@router.get("/{campaign_id}/visual-assets/{asset_id}")
def get_visual_asset(campaign_id: str, asset_id: str) -> Response:
    _require_enabled()
    if not is_valid_asset_id(asset_id):
        raise HTTPException(status_code=404, detail="Unknown asset.")
    _require_campaign(campaign_id)

    asset = get_store().get_asset(campaign_id, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Unknown asset.")

    return Response(
        content=asset.data,
        media_type=asset.media_type,
        headers={
            # Asset ids are never reused, so this is safe and keeps the demo
            # from re-fetching an image on every turn.
            "Cache-Control": "private, max-age=31536000, immutable"
        },
    )
