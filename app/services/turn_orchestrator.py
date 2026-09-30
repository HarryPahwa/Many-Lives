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
import re
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.api.schemas import (
    ContextManifest,
    ModelCall,
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
from app.domain.mutations import MutationBundle
from app.domain.mutation_validator import apply_mutation_bundle, validate_candidate_bundle
from app.domain.picker import select_winning_candidate
from app.domain.rng import TurnRng
from app.domain.rules import Resolution
from app.domain.types import ActionClass
from app.harness.candidate_generator import (
    load_candidate_count,
    load_candidate_pipeline_mode,
    load_candidate_validation_retries,
)
from app.harness.jev_scorer import JevScoringError, load_runtime_rules
from app.harness.model_client import ModelOutputError
from app.harness.mutation_compiler import validate_compilation_fidelity
from app.harness.outcome_generator import (
    CandidateOutcome,
    mentioned_target_ids,
    validate_outcome,
)
from app.services.turn_trace_logger import log_turn_trace

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

_SOCIAL_ACTIONS = frozenset({"TALK", "PERSUADE", "DECEIVE", "INTIMIDATE"})

# Test-only shortcuts; honoured only with DEBUG_ENDPOINTS=true.
_DEBUG_DIE_COMMANDS = frozenset({"/die", "debug die", "kill myself"})
_DEBUG_TARGET = re.compile(
    r"^(murder|reanimate)\s+(?:the\s+)?(\S.*)$", re.IGNORECASE
)


def _debug_target_command(text: str) -> tuple[str, str] | None:
    match = _DEBUG_TARGET.match(text.strip())
    if match is None:
        return None
    query = match.group(2).strip()
    if not query:
        return None
    return match.group(1).lower(), query


def _debug_murder_query(text: str) -> str | None:
    parsed = _debug_target_command(text)
    if parsed is None or parsed[0] != "murder":
        return None
    return parsed[1]


def _model_world(snapshot, context_text: str) -> dict:
    """One canonical model view shared by generation and Jev scoring."""
    from app.persistence.views import thaw

    player = thaw(snapshot.player)
    characters = [thaw(character) for character in snapshot.characters]
    owned_items = [thaw(item) for item in snapshot.owned_items]
    player_id = player.get("entity_id") or player.get("id")

    def inventory_for(owner_id: str) -> list[dict]:
        return [
            item for item in owned_items
            if item.get("location", {}).get("ref_id") == owner_id
        ]

    for character in characters:
        owner_id = character.get("entity_id") or character.get("id")
        character["inventory"] = inventory_for(owner_id)

    return {
        "player": player,
        "player_inventory": inventory_for(player_id),
        "current_cell": thaw(snapshot.current_cell),
        "characters": characters,
        "items": [thaw(item) for item in snapshot.items],
        "container_items": [thaw(item) for item in snapshot.container_items],
        "config": thaw(snapshot.config),
        "policy_context": context_text,
    }


def _fallback_adjudication_snapshot(engine: EnginePort, view: WorldView):
    """Build canonical-shaped state for non-persistent test/demo engines."""
    from app.persistence.views import WorldView as PersistenceWorldView, freeze

    sheet = engine.player_sheet(view.campaign_id, view.player_id)
    owned_items = []
    for item in sheet.carried:
        owned_items.append({
            "id": item.id,
            "entity_id": item.id,
            "name": item.name,
            "entity_type": "ITEM",
            "item": {"quantity": item.quantity},
            "location": {"kind": "INVENTORY", "ref_id": view.player_id, "slot": None},
            "version": 0,
        })
    for slot, item in (("WEAPON", sheet.weapon), ("ARMOR", sheet.armor)):
        if item is not None:
            owned_items.append({
                "id": item.id,
                "entity_id": item.id,
                "name": item.name,
                "entity_type": "ITEM",
                "item": {"quantity": item.quantity, "subtype": slot},
                "location": {"kind": "EQUIPPED", "ref_id": view.player_id, "slot": slot},
                "version": 0,
            })
    return freeze(PersistenceWorldView(
        campaign={"id": view.campaign_id, "turn_count": view.current_turn, "version": 0},
        player={
            "id": view.player_id,
            "entity_id": view.player_id,
            "character": {
                "hp": sheet.hp, "max_hp": sheet.max_hp,
                "mp": sheet.mp, "max_mp": sheet.max_mp,
                "attack": sheet.stats.attack, "defense": sheet.stats.defense,
                "speed": sheet.stats.speed, "dodge_pct": sheet.stats.dodge_pct,
                "skill": sheet.stats.skill, "status": sheet.status,
                "physical_conditions": list(sheet.physical_conditions),
                "mental_conditions": list(sheet.mental_conditions),
            },
            "version": 0,
        },
        current_cell={"id": view.visible_cell.cell_id, "version": 0},
        destination_cell=None,
        characters=tuple({
            "id": character.id,
            "entity_id": character.id,
            "name": character.name,
            "entity_type": character.entity_type,
            "character": {
                "hp": getattr(character, "hp", 8),
                "max_hp": getattr(character, "max_hp", 8),
                "status": character.status,
                "disposition": character.disposition,
                "physical_conditions": list(character.physical_conditions),
                "mental_conditions": list(character.mental_conditions),
            },
            "version": 0,
        } for character in view.visible_cell.characters),
        items=tuple({
            "id": item.id, "entity_id": item.id, "name": item.name,
            "entity_type": "ITEM", "where": item.where, "version": 0,
        } for item in view.visible_cell.items),
        container_items=(),
        config={},
        owned_items=tuple(owned_items),
    ))


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

        # 2/3. Preserve deterministic commands; use the JEV pipeline only
        # for input that the structural parser cannot interpret.
        intent = engine.parse_fast_path(request.input, request.player_id)
        jev_bundle: MutationBundle | None = None
        manifest: ContextManifest | None = None

        from app.config import get_settings

        debug = get_settings().debug_endpoints
        debug_command = _debug_target_command(request.input) if debug else None
        fast_resolution: EngineResolution | None = None
        if intent is not None:
            if intent.action_type in _SOCIAL_ACTIONS:
                intent.params.setdefault("utterance", request.input[:300])
            fast_resolution = engine.resolve(view, intent)

        if debug and request.input.strip().lower() in _DEBUG_DIE_COMMANDS:
            intent = Intent("WAIT", request.player_id, params={"debug_die": 1})
            record.path = "DEBUG"
            resolution = engine.resolve(view, intent)
        elif debug_command and debug_command[0] == "murder":
            intent = Intent(
                "ATTACK",
                request.player_id,
                params={"query": debug_command[1], "debug_murder": True},
            )
            record.path = "DEBUG"
            resolution = engine.resolve(view, intent)
        elif debug_command and debug_command[0] == "reanimate":
            intent = Intent(
                "WAIT",
                request.player_id,
                params={"query": debug_command[1], "debug_reanimate": True},
            )
            record.path = "DEBUG"
            resolution = engine.resolve(view, intent)
        elif debug and request.input.startswith("/fast "):
            # Fast-path debug endpoint override
            fast_input = request.input[len("/fast "):].strip()
            intent = engine.parse_fast_path(fast_input, request.player_id)
            if intent is not None:
                record.path = "FAST_DEBUG"
                resolution = engine.resolve(view, intent)
            else:
                resolution = EngineResolution(accepted=False, reason="Invalid fast debug syntax")
        elif (
            intent is not None
            and fast_resolution is not None
            and not fast_resolution.retry_with_model
        ):
            record.path = "FAST"
            resolution = fast_resolution
        else:
            record.path = "JEV_PIPELINE"
            action_class = harness.classify(request.input, view)
            record.action_class = action_class
            context_text, manifest, record.vector_search_ms = harness.build_context(
                view, action_class, request.input
            )
            record.context_manifest = manifest.model_dump()

            # Generation, validation, and Jev must reason over the same
            # canonical snapshot.  The durable engine includes full mechanics
            # and inventory for the player and every visible character.
            snapshot_loader = getattr(engine, "load_adjudication_snapshot", None)
            snapshot = (
                snapshot_loader(campaign_id, request.player_id)
                if snapshot_loader is not None
                else _fallback_adjudication_snapshot(engine, view)
            )
            model_world = _model_world(snapshot, context_text)

            # Step A: Candidate Generation
            pipeline_mode = load_candidate_pipeline_mode()
            candidate_count = load_candidate_count()
            max_validation_retries = load_candidate_validation_retries()
            candidate_trace: list[dict[str, Any]] = []
            filter_results: list[dict[str, Any]] = []
            surviving_candidates: list[Any] = []
            required_target_ids = mentioned_target_ids(request.input, model_world)

            validation_feedback: list[str] | None = None
            for generation_attempt in range(1, max_validation_retries + 2):
                try:
                    if pipeline_mode == "two_step":
                        generation_res = harness.outcome_generator.generate_outcomes(
                            request.input,
                            model_world,
                            actor_id=request.player_id,
                            action_class=ActionClass(action_class),
                            required_target_ids=required_target_ids,
                            candidate_count=candidate_count,
                            validation_feedback=validation_feedback,
                        )
                        generator = harness.outcome_generator
                    else:
                        generation_res = harness.candidate_generator.generate_candidates(
                            request.input,
                            model_world,
                            candidate_count=candidate_count,
                            validation_feedback=validation_feedback,
                        )
                        generator = harness.candidate_generator
                except ModelOutputError:
                    logger.warning("candidate generation failed", exc_info=True)
                    resolution = EngineResolution(
                        accepted=False,
                        reason="No safe action candidates could be generated.",
                    )
                    return self._finish_rejected(
                        view, record, resolution, "CANDIDATE_GENERATION_FAILED", manifest
                    )
                generation_call = getattr(generator, "last_result", None)
                if generation_call is not None:
                    record.model_calls.append(
                        ModelCall(
                            role="CANDIDATE_GENERATOR",
                            model=generation_call.model,
                            input_tokens=generation_call.usage.get("input_tokens", 0),
                            output_tokens=generation_call.usage.get("output_tokens", 0),
                            latency_ms=generation_call.latency_ms,
                            attempts=generation_call.attempts,
                            schema_valid=True,
                            retry_errors=generation_call.retry_errors,
                        ).model_dump()
                    )

                attempt_filters: list[dict[str, Any]] = []
                surviving_candidates = []
                for cand in generation_res.candidates:
                    if pipeline_mode == "two_step":
                        is_valid, reason = validate_outcome(
                            cand,
                            model_world,
                            actor_id=request.player_id,
                            action_class=ActionClass(action_class),
                            required_target_ids=required_target_ids,
                        )
                    else:
                        is_valid, reason = validate_candidate_bundle(snapshot, cand)
                    candidate_trace.append({
                        **cand.model_dump(mode="json"),
                        "generation_attempt": generation_attempt,
                    })
                    result = {
                        "bundle_id": cand.bundle_id,
                        "generation_attempt": generation_attempt,
                        "valid": is_valid,
                        "rejection_reason": reason,
                    }
                    attempt_filters.append(result)
                    filter_results.append(result)
                    if is_valid:
                        surviving_candidates.append(cand)
                if surviving_candidates:
                    break
                validation_feedback = sorted({
                    str(result["rejection_reason"])
                    for result in attempt_filters
                    if result["rejection_reason"]
                })[:10]

            if not surviving_candidates:
                # All candidates filtered out
                resolution = EngineResolution(
                    accepted=False,
                    reason=filter_results[0]["rejection_reason"] if filter_results else "No valid action candidates could be performed.",
                )
                log_turn_trace({
                    "pipeline_mode": pipeline_mode,
                    "campaign_id": campaign_id,
                    "turn_id": request.turn_id,
                    "player_input": request.input,
                    "candidates": candidate_trace,
                    "filter_results": filter_results,
                    "winning_bundle": None,
                    "accepted": False,
                })
                return self._finish_rejected(view, record, resolution, "SCHEMA_FILTER_REJECTED", manifest)

            # Step B: Jev Semantic Scoring
            try:
                scoring_res = harness.jev_scorer.score_candidates(
                    request.input,
                    surviving_candidates,
                    model_world,
                    runtime_rules=load_runtime_rules(),
                )
            except JevScoringError:
                logger.warning("JEV scoring failed", exc_info=True)
                resolution = EngineResolution(
                    accepted=False,
                    reason="The action could not be judged safely.",
                )
                return self._finish_rejected(
                    view, record, resolution, "JEV_SCORING_FAILED", manifest
                )
            jev_latency = getattr(harness.jev_scorer, "last_latency_ms", None)
            if jev_latency is not None:
                jev_usage = getattr(harness.jev_scorer, "last_usage", {})
                record.model_calls.append(
                    ModelCall(
                        role="JEV",
                        model=getattr(getattr(harness.jev_scorer, "settings", None), "model_jev", "typesafe/jev"),
                        input_tokens=int(jev_usage.get("input_tokens", 0) or 0),
                        output_tokens=int(jev_usage.get("output_tokens", 0) or 0),
                        latency_ms=jev_latency,
                        attempts=1,
                        schema_valid=True,
                    ).model_dump()
                )
            normalized_weights = scoring_res.normalized_weights()

            # Step C: Weighted RNG Selection
            campaign_seed = getattr(summary, "seed", 0) if hasattr(summary, "seed") else 42
            if isinstance(campaign_seed, str):
                try:
                    campaign_seed = int(campaign_seed)
                except ValueError:
                    campaign_seed = hash(campaign_seed)
            turn_rng = TurnRng(campaign_seed or 42, view.current_turn)
            winning_candidate = select_winning_candidate(
                surviving_candidates, normalized_weights, turn_rng
            )
            selected_outcome: CandidateOutcome | None = None
            compilation_trace: list[dict[str, Any]] = []
            if pipeline_mode == "two_step":
                selected_outcome = winning_candidate
                compiled_bundle: MutationBundle | None = None
                compilation_feedback: list[str] | None = None
                for compilation_attempt in range(1, max_validation_retries + 2):
                    try:
                        candidate_bundle = harness.mutation_compiler.compile(
                            selected_outcome,
                            model_world,
                            validation_feedback=compilation_feedback,
                        )
                    except ModelOutputError:
                        logger.warning("mutation compilation failed", exc_info=True)
                        candidate_bundle = None
                        fidelity_valid, fidelity_reason = False, "MUTATION_COMPILATION_FAILED"
                        mutation_valid, mutation_reason = False, "MUTATION_COMPILATION_FAILED"
                    else:
                        compilation_call = getattr(
                            harness.mutation_compiler, "last_result", None
                        )
                        if compilation_call is not None:
                            record.model_calls.append(
                                ModelCall(
                                    role="MUTATION_COMPILER",
                                    model=compilation_call.model,
                                    input_tokens=compilation_call.usage.get(
                                        "input_tokens", 0
                                    ),
                                    output_tokens=compilation_call.usage.get(
                                        "output_tokens", 0
                                    ),
                                    latency_ms=compilation_call.latency_ms,
                                    attempts=compilation_call.attempts,
                                    schema_valid=True,
                                    retry_errors=compilation_call.retry_errors,
                                ).model_dump()
                            )
                        fidelity_valid, fidelity_reason = validate_compilation_fidelity(
                            selected_outcome, candidate_bundle
                        )
                        mutation_valid, mutation_reason = validate_candidate_bundle(
                            snapshot, candidate_bundle
                        )
                    compilation_trace.append(
                        {
                            "attempt": compilation_attempt,
                            "bundle": (
                                candidate_bundle.model_dump(mode="json")
                                if candidate_bundle is not None
                                else None
                            ),
                            "fidelity_valid": fidelity_valid,
                            "fidelity_reason": fidelity_reason,
                            "mutation_valid": mutation_valid,
                            "mutation_reason": mutation_reason,
                        }
                    )
                    if candidate_bundle is not None and fidelity_valid and mutation_valid:
                        compiled_bundle = candidate_bundle
                        break
                    compilation_feedback = [
                        reason
                        for reason in (fidelity_reason, mutation_reason)
                        if reason
                    ]
                if compiled_bundle is None:
                    resolution = EngineResolution(
                        accepted=False,
                        reason="The selected outcome could not be compiled safely.",
                    )
                    log_turn_trace(
                        {
                            "pipeline_mode": pipeline_mode,
                            "campaign_id": campaign_id,
                            "turn_id": request.turn_id,
                            "player_input": request.input,
                            "candidates": candidate_trace,
                            "filter_results": filter_results,
                            "scoring": scoring_res.model_dump(mode="json"),
                            "selected_outcome": selected_outcome.model_dump(mode="json"),
                            "compilation": compilation_trace,
                            "winning_bundle": None,
                            "accepted": False,
                        }
                    )
                    return self._finish_rejected(
                        view, record, resolution, "MUTATION_COMPILATION_FAILED", manifest
                    )
                winning_bundle = compiled_bundle
            else:
                winning_bundle = winning_candidate
            jev_bundle = winning_bundle

            # Step D: Apply Winning Mutation Bundle
            if hasattr(engine, "resolve_mutation_bundle"):
                resolution = engine.resolve_mutation_bundle(view, winning_bundle, turn_id=request.turn_id)
            else:
                resolution = apply_mutation_bundle(snapshot, winning_bundle, turn_id=request.turn_id)
                resolution = EngineResolution(
                    accepted=resolution.accepted,
                    reason=resolution.reason,
                    effects=[m.model_dump(mode="json") for m in winning_bundle.mutations],
                    event_types=[e.type.value for e in resolution.events],
                    outcome_summary=resolution.outcome_summary,
                    pending=(snapshot, winning_bundle),
                )

            # Log Turn Trace
            log_turn_trace({
                "pipeline_mode": pipeline_mode,
                "campaign_id": campaign_id,
                "turn_id": request.turn_id,
                "player_input": request.input,
                "candidates": candidate_trace,
                "filter_results": filter_results,
                "scoring": scoring_res.model_dump(mode="json"),
                "normalized_weights": normalized_weights,
                "selected_outcome": (
                    selected_outcome.model_dump(mode="json") if selected_outcome else None
                ),
                "compilation": compilation_trace,
                "winning_bundle": winning_bundle.model_dump(mode="json"),
                "accepted": resolution.accepted,
            })
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
            if jev_bundle is not None and hasattr(engine, "resolve_mutation_bundle"):
                resolution = engine.resolve_mutation_bundle(
                    view, jev_bundle, turn_id=request.turn_id
                )
            elif intent is not None:
                resolution = engine.resolve(view, intent)
            else:
                raise
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
        # The engine's reason is authoritative; the player must see it even if
        # the narrator model ignores it.
        if resolution.reason and not narration.startswith(resolution.reason):
            narration = f"{resolution.reason} {narration}"
            if record.narration is not None:
                record.narration["prose"] = narration
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
