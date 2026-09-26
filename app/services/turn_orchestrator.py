"""Turn orchestrator (TDD §7.1) — the integration seam (Developer C).

Runs the fixed turn lifecycle: receive -> replay-check -> parse/adjudicate ->
resolve -> commit -> post-commit -> narrate -> respond. It holds the
per-campaign lock (§6.4) and never writes canonical state itself: every
mutation goes through the engine seam, and no model call happens inside the
commit (§7.1, §9.9).

The orchestrator talks only to ``EnginePort`` / ``HarnessPort`` from
``app.services.stubs``, so the stub and the real implementations are
interchangeable.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone

from app.api.schemas import (
    ContextManifest,
    Outcome,
    ResumeResult,
    TurnRequest,
    TurnResult,
)
from app.services.stubs import (
    ConcurrencyConflict,
    EnginePort,
    EngineResolution,
    HarnessPort,
    Intent,
    TurnRecord,
    WorldView,
    get_engine,
    get_harness,
)

logger = logging.getLogger("many_lives.turn")

# One lock per campaign (§6.4): a campaign never has two turns in flight.
# Held for the whole request, model calls included.
_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)
_LOCKS_GUARD = threading.Lock()


def _lock_for(campaign_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS[campaign_id]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# A turn in any of these states has already had its effects applied (or was
# rejected and applied none). It must never be resolved or committed again.
_APPLIED_STATUSES = frozenset(
    {"COMMITTED", "NARRATED", "NARRATION_FAILED", "REJECTED"}
)


class CampaignNotFound(LookupError):
    """Unknown campaign -> HTTP 404 (§17.1)."""


class CampaignNotActive(RuntimeError):
    """Campaign is finished or not this player's turn -> HTTP 409 (§17.1)."""


class TurnOrchestrator:
    """Executes the §7.1 state machine for one server process."""

    def __init__(
        self, engine: EnginePort | None = None, harness: HarnessPort | None = None
    ) -> None:
        self._engine = engine
        self._harness = harness

    # The seams are resolved per call, not cached, so A and B can swap an
    # implementation in at runtime without restarting the orchestrator.
    @property
    def engine(self) -> EnginePort:
        return self._engine or get_engine()

    @property
    def harness(self) -> HarnessPort:
        return self._harness or get_harness()

    # ------------------------------------------------------------------
    # Turn (§7.1)
    # ------------------------------------------------------------------

    def take_turn(self, campaign_id: str, request: TurnRequest) -> TurnResult:
        with _lock_for(campaign_id):
            return self._take_turn_locked(campaign_id, request)

    def _take_turn_locked(self, campaign_id: str, request: TurnRequest) -> TurnResult:
        engine, harness = self.engine, self.harness

        # 1. Receive. Unknown campaign is a 404 before anything else.
        summary = self._require_campaign(campaign_id)

        # 1b. Replay. A turn_id that already reached COMMITTED or NARRATED
        #     returns its stored result and applies nothing (§7.1.1, §9.9).
        #     A record left at RECEIVED was never committed and may be retried
        #     (§21, "process terminated"), so it deliberately falls through.
        existing = engine.get_turn(campaign_id, request.turn_id)
        if existing is not None and existing.status in _APPLIED_STATUSES:
            logger.info(
                "turn replay",
                extra={"campaign_id": campaign_id, "turn_id": request.turn_id},
            )
            if existing.result is not None:
                return TurnResult.model_validate(existing.result)
            # Committed, but the process died before the result was stored.
            # The effects are already applied, so re-resolving would apply
            # them twice: regenerate the response from current state instead
            # (§7.1.7 allows a later GET to regenerate prose).
            return self._regenerate_result(existing, request.player_id)

        if summary.status != "ACTIVE":
            raise CampaignNotActive(campaign_id)

        view = engine.load_world_view(campaign_id, request.player_id)

        record = TurnRecord(
            campaign_id=campaign_id,
            turn_id=request.turn_id,
            kind="ACTION",
            player_id=request.player_id,
            status="RECEIVED",
            input=request.input,
        )
        engine.put_turn(record)

        # 2/3. Parse (fast path) or adjudicate (free-form).
        intent = engine.parse_fast_path(request.input, request.player_id)
        manifest: ContextManifest | None = None

        if intent is not None:
            record.path = "FAST"
        else:
            record.path = "ADJUDICATED"
            action_class = harness.classify(request.input, view)
            record.action_class = action_class
            context_text, manifest, record.vector_search_ms = harness.build_context(
                view, action_class, request.input
            )
            record.context_manifest = manifest.model_dump()

            proposal, calls = harness.adjudicate(request.input, context_text, view, action_class)
            record.model_calls.extend(c.model_dump() for c in calls)
            if proposal is None:
                # §7.1.3 / §21: invalid structured output after retries.
                resolution = EngineResolution(
                    accepted=False,
                    reason="You try, but nothing about that succeeds.",
                )
                return self._finish_rejected(
                    view, record, resolution, "ADJUDICATION_FAILED", manifest
                )
            record.proposal = {
                "action_type": proposal.action_type,
                "actor_id": proposal.actor_id,
                "targets": proposal.targets,
                "feasibility": proposal.feasibility,
                "reason": proposal.reason,
                "proposed_effects_on_success": proposal.proposed_effects_on_success,
                "proposed_effects_on_failure": proposal.proposed_effects_on_failure,
                "utterance": proposal.utterance,
            }
            # The proposal is data, not authority: the engine re-validates
            # every precondition below (§5.1, §10.3).
            intent = Intent(
                action_type=proposal.action_type,
                actor_id=proposal.actor_id,
                targets=list(proposal.targets),
                params=dict(proposal.params),
            )

        # 4. Resolve.
        resolution = engine.resolve(view, intent)
        record.rejected_effects = list(resolution.rejected_effects)

        if not resolution.accepted:
            # A rejected turn is narrated, but nothing is committed (§7.1).
            return self._finish_rejected(view, record, resolution, "REJECTED", manifest)

        # 5. Commit. One version-checked write; no model call inside it.
        try:
            commit = engine.commit_turn(view, resolution, request.turn_id)
        except ConcurrencyConflict:
            # §7.1.5: reload and re-resolve once, then surface 409.
            view = engine.load_world_view(campaign_id, request.player_id)
            resolution = engine.resolve(view, intent)
            if not resolution.accepted:
                return self._finish_rejected(view, record, resolution, "REJECTED", manifest)
            commit = engine.commit_turn(view, resolution, request.turn_id)

        record.status = "COMMITTED"
        record.turn_sequence = commit.turn_sequence
        record.event_ids = commit.event_ids
        record.accepted_effect_types = [str(e.get("type")) for e in resolution.effects]
        record.committed_at = _now()
        engine.put_turn(record)

        # 6/7. Post-commit: narrate from committed state. A narrator failure
        #      must not undo the commit (§7.1.7, §21).
        after = engine.load_world_view(campaign_id, request.player_id)
        narration, source = self._narrate(
            after, resolution, "ACTION", record, events=commit.events
        )

        result = self._build_result(
            record.turn_id,
            commit.turn_sequence,
            status=record.status,
            accepted=True,
            reason=None,
            narration=narration,
            source=source,
            resolution=resolution,
            view=after,
        )
        record.result = result.model_dump()
        record.narrated_at = _now()
        engine.put_turn(record)
        logger.info(
            "turn committed",
            extra={
                "campaign_id": campaign_id,
                "turn_id": record.turn_id,
                "turn_sequence": commit.turn_sequence,
                "path": record.path,
            },
        )
        return result

    # ------------------------------------------------------------------
    # Resume (§7.4)
    # ------------------------------------------------------------------

    def resume(self, campaign_id: str, player_id: str) -> ResumeResult:
        with _lock_for(campaign_id):
            engine, harness = self.engine, self.harness
            summary = self._require_campaign(campaign_id)
            view = engine.load_world_view(campaign_id, player_id)

            _, manifest, _ = harness.build_context(view, "RESUME", None)
            record = TurnRecord(
                campaign_id=campaign_id,
                turn_id=f"resume-{_now()}-{view.current_turn}",
                kind="RESUME",
                player_id=player_id,
                status="RECEIVED",
                path="RESUME",
                turn_sequence=view.current_turn,
                context_manifest=manifest.model_dump(),
            )
            # Resume does not mutate world state; it writes only a metrics
            # record of kind RESUME (§7.4).
            narration, source = self._narrate(
                view, EngineResolution(accepted=True), "RESUME", record
            )
            record.status = "NARRATED"
            record.narrated_at = _now()
            engine.put_turn(record)

            from app.config import get_settings

            return ResumeResult(
                campaign=summary,
                player=view.player,
                visible_cell=view.visible_cell,
                map=engine.build_map(campaign_id, player_id),
                narration=narration,
                narration_source=source,
                manifest=manifest,
                debug_available=get_settings().debug_endpoints,
                visuals_enabled=get_settings().enable_room_visuals,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _regenerate_result(self, record: TurnRecord, player_id: str) -> TurnResult:
        """Rebuild a TurnResult for a turn that committed but stored no result.

        Applies nothing: it reads current state and re-narrates, which is the
        regeneration path §7.1.7 allows after a narrator failure.
        """
        view = self.engine.load_world_view(record.campaign_id, player_id)
        resolution = EngineResolution(
            accepted=record.status != "REJECTED",
            reason=None,
            effects=[{"type": t} for t in record.accepted_effect_types],
            event_types=[],
            outcome_summary="",
        )
        narration, source = self._narrate(view, resolution, "ACTION", record)
        result = self._build_result(
            record.turn_id,
            record.turn_sequence or view.current_turn,
            status=record.status,
            accepted=resolution.accepted,
            reason=None,
            narration=narration,
            source=source,
            resolution=resolution,
            view=view,
        )
        record.result = result.model_dump()
        self.engine.put_turn(record)
        return result

    def _require_campaign(self, campaign_id: str):
        summary = self.engine.get_campaign(campaign_id)
        if summary is None:
            raise CampaignNotFound(campaign_id)
        return summary

    def _narrate(
        self,
        view: WorldView,
        resolution: EngineResolution,
        kind: str,
        record: TurnRecord,
        events: list[dict] | None = None,
    ) -> tuple[str, str]:
        """Narrate, falling back to deterministic template text on failure.

        The state is already committed at this point, so a narrator error may
        never raise out of here (§7.1.7).
        """
        try:
            narration = self.harness.narrate(view, resolution, kind, events)
            if narration.model_call is not None:
                record.model_calls.append(narration.model_call.model_dump())
            record.narration = {
                "prose": narration.prose,
                "claims": narration.claims,
                "source": narration.source,
            }
            record.status = "NARRATED"
            return narration.prose, narration.source
        except Exception:  # noqa: BLE001 - a narrator failure must not fail the turn
            logger.exception(
                "narration failed; using template",
                extra={"campaign_id": view.campaign_id, "turn_id": record.turn_id},
            )
            try:
                prose = self.harness.template_narration(view, resolution, kind, events)
            except Exception:  # noqa: BLE001 - last-resort deterministic text
                prose = resolution.outcome_summary or "You steady yourself."
            record.narration = {"prose": prose, "claims": [], "source": "TEMPLATE"}
            record.status = "NARRATION_FAILED"
            return prose, "TEMPLATE"

    def _finish_rejected(
        self,
        view: WorldView,
        record: TurnRecord,
        resolution: EngineResolution,
        reason_code: str,
        manifest: ContextManifest | None,
    ) -> TurnResult:
        """A rejected turn is narrated and recorded, but changes no state."""
        narration, source = self._narrate(view, resolution, "ACTION", record)
        record.status = "REJECTED"
        record.reason_code = reason_code
        result = self._build_result(
            record.turn_id,
            view.current_turn,
            status="REJECTED",
            accepted=False,
            reason=resolution.reason or reason_code,
            narration=narration,
            source=source,
            resolution=resolution,
            view=view,
        )
        record.result = result.model_dump()
        record.narrated_at = _now()
        self.engine.put_turn(record)
        logger.info(
            "turn rejected",
            extra={
                "campaign_id": view.campaign_id,
                "turn_id": record.turn_id,
                "reason": reason_code,
            },
        )
        return result

    def _build_result(
        self,
        turn_id: str,
        turn_sequence: int,
        *,
        status: str,
        accepted: bool,
        reason: str | None,
        narration: str,
        source: str,
        resolution: EngineResolution,
        view: WorldView,
    ) -> TurnResult:
        from app.config import get_settings

        return TurnResult(
            turn_id=turn_id,
            turn_sequence=turn_sequence,
            status=status,
            accepted=accepted,
            reason=reason,
            narration=narration,
            narration_source="TEMPLATE" if source == "TEMPLATE" else "MODEL",
            outcome=Outcome(
                summary=resolution.outcome_summary or (reason or ""),
                events=list(resolution.event_types),
                rolls=list(resolution.rolls),
            ),
            player=view.player,
            visible_cell=view.visible_cell,
            campaign_status=view.campaign_status,
            debug_available=get_settings().debug_endpoints,
            visuals_enabled=get_settings().enable_room_visuals,
        )


_ORCHESTRATOR = TurnOrchestrator()


def get_orchestrator() -> TurnOrchestrator:
    return _ORCHESTRATOR
