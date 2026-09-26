"""Room visuals service (Room Visuals §10.2).

Everything the routes need, and the only place the image client is called.

Two rules hold this together:

* The scene is read under the campaign lock so it is never half a turn, but the
  lock is **released before any image call** — a model call takes ~10 s and
  holding the turn lock for that would stall play (§7.3).
* ``render`` never raises. It runs in a background task after the response has
  been sent; an exception there would be invisible, so every failure becomes a
  FAILED record with a short code instead (§10.2, VIS-07).
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.image_client import ImageGenerationError, get_client
from app.services.visual_spec import (
    VisualSceneSpec,
    build_spec,
    edit_prompt,
    generate_prompt,
    signature_of,
)
from app.services.visual_store import (
    STATUS_FAILED,
    STATUS_GENERATING,
    STATUS_NONE,
    STATUS_READY,
    VisualAsset,
    VisualRecord,
    get_store,
    new_asset_id,
)

logger = logging.getLogger("many_lives.visuals")


def _scene_for(engine: Any, campaign_id: str, cell_id: str) -> dict[str, Any] | None:
    """Read a consistent scene, or None when the cell may not be pictured.

    Prefers the engine's own ``visual_scene``. Developer A's engine is not
    obliged to implement it (§7.2), so there is a fallback that uses the
    world view — that path cannot supply HP, so every band is UNKNOWN and
    health changes will not mark the image out of date.
    """
    from app.services.turn_orchestrator import _lock_for

    with _lock_for(campaign_id):
        reader = getattr(engine, "visual_scene", None)
        if callable(reader):
            return reader(campaign_id, cell_id)

        # Fallback: current cell only, no HP.
        try:
            view = engine.load_world_view(campaign_id, "player_1")
        except Exception:  # noqa: BLE001 - unknown campaign or engine shape
            return None
        cell = view.visible_cell
        if cell.cell_id != cell_id:
            return None
        return {
            "cell_id": cell.cell_id,
            "name": cell.name,
            "description": cell.description,
            "features": [
                {"id": f.id, "name": f.name, "state": dict(f.state or {})}
                for f in cell.features
            ],
            "characters": [
                {
                    "id": c.id,
                    "name": c.name,
                    "status": c.status,
                    "hp": None,
                    "max_hp": None,
                    "description": "",
                }
                for c in cell.characters
            ],
            "items": [
                {"id": i.id, "name": i.name, "where": i.where} for i in cell.items
            ],
        }


def current_spec(engine: Any, campaign_id: str, cell_id: str) -> VisualSceneSpec | None:
    scene = _scene_for(engine, campaign_id, cell_id)
    if scene is None:
        return None
    return build_spec(cell_id, scene)


def status_for(
    engine: Any, campaign_id: str, cell_id: str
) -> tuple[VisualRecord | None, VisualSceneSpec | None, bool]:
    """Return (record, current spec, dirty)."""
    spec = current_spec(engine, campaign_id, cell_id)
    if spec is None:
        return None, None, False
    record = get_store().get(campaign_id, cell_id)
    dirty = bool(
        record
        and record.status == STATUS_READY
        and record.signature != signature_of(spec)
    )
    return record, spec, dirty


def render(campaign_id: str, cell_id: str, spec: VisualSceneSpec) -> None:
    """Generate or edit, persist, and update the record. Never raises.

    Runs as a background task, so the only way to report anything is the
    record itself.
    """
    store = get_store()
    record = store.get(campaign_id, cell_id) or VisualRecord(
        campaign_id=campaign_id, cell_id=cell_id
    )
    try:
        from app.config import get_settings

        settings = get_settings()
        client = get_client()

        previous_asset = (
            store.get_asset(campaign_id, record.current_asset_id)
            if record.current_asset_id
            else None
        )
        # Edit whenever there is something to edit from and drift is capped;
        # after VISUAL_MAX_EDITS, regenerate so errors do not accumulate.
        #
        # Note the status is NOT part of this test: the claim sets the record to
        # GENERATING before render runs, so requiring READY here would mean the
        # edit path never fired and every update silently regenerated from
        # scratch, throwing away the visual continuity that is its whole point.
        use_edit = (
            previous_asset is not None
            and record.edits_since_base < settings.visual_max_edits
        )

        if use_edit:
            old_spec = (
                VisualSceneSpec.model_validate(record.spec) if record.spec else None
            )
            prompt = edit_prompt(old_spec, spec)
            references = [previous_asset.data]
            kind = "EDIT"
        else:
            prompt = generate_prompt(spec)
            references = []
            kind = "GENERATE"

        result = client.generate(prompt, references=references)

        asset_id = new_asset_id()
        revision = record.revision + 1
        signature = signature_of(spec)
        store.put_asset(
            VisualAsset(
                asset_id=asset_id,
                campaign_id=campaign_id,
                cell_id=cell_id,
                revision=revision,
                signature=signature,
                media_type=result.media_type,
                data=result.data,
                size=len(result.data),
                model=result.model,
                kind=kind,
            )
        )

        record.status = STATUS_READY
        record.revision = revision
        record.signature = signature
        record.spec = spec.model_dump(mode="json")
        record.current_asset_id = asset_id
        record.error_code = None
        record.model = result.model
        if kind == "GENERATE":
            record.base_asset_id = asset_id
            record.edits_since_base = 0
        else:
            record.edits_since_base += 1
        store.put(record)

        store.prune(campaign_id, cell_id, settings.visual_keep_revisions)
        logger.info(
            "room visual %s ok: %s rev=%d bytes=%d cost=%s",
            kind.lower(),
            cell_id,
            revision,
            len(result.data),
            result.cost,
        )
    except ImageGenerationError as exc:
        # The code is safe to store and show; the provider's text is not.
        record.status = STATUS_FAILED
        record.error_code = exc.code
        store.put(record)
        logger.warning("room visual failed for %s: %s", cell_id, exc.code)
    except Exception as exc:  # noqa: BLE001 - a background task may not die quietly
        record.status = STATUS_FAILED
        record.error_code = "INTERNAL"
        store.put(record)
        logger.exception("room visual crashed for %s: %s", cell_id, type(exc).__name__)


def image_url_for(campaign_id: str, record: VisualRecord | None) -> str | None:
    if record is None or not record.current_asset_id:
        return None
    return f"/api/campaigns/{campaign_id}/visual-assets/{record.current_asset_id}"


def status_payload(
    campaign_id: str, cell_id: str, record: VisualRecord | None, dirty: bool
) -> dict[str, Any]:
    from app.config import get_settings

    return {
        "cell_id": cell_id,
        "status": record.status if record else STATUS_NONE,
        "dirty": dirty,
        "revision": record.revision if record else 0,
        # Served even while GENERATING or FAILED so the UI keeps the previous
        # picture on screen instead of flashing to empty.
        "image_url": image_url_for(campaign_id, record),
        "auto_update": get_settings().auto_update_room_visuals,
        "error_code": record.error_code if record else None,
    }


__all__ = [
    "STATUS_FAILED",
    "STATUS_GENERATING",
    "STATUS_NONE",
    "STATUS_READY",
    "current_spec",
    "image_url_for",
    "render",
    "status_for",
    "status_payload",
]
