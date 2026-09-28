"""Adapter from B's domain harness to C's integration seam.

This module is deliberately in ``services``: it composes the API-facing seam
with pure harness functions without making ``app.harness`` depend on services.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from time import monotonic
from typing import Any

from app.api.schemas import ContextManifest as WireContextManifest
from app.api.schemas import MemoryRef, ModelCall
from app.domain.types import (
    ActionClass,
    CellSnapshot,
    Event,
    EventType,
    PlayerSummary,
    Role,
    SnapshotEntity,
    SocialContext,
)
from app.harness.adjudicator import adjudicate as domain_adjudicate
from app.harness.candidate_generator import ModelCandidateGenerator
from app.harness.context_builder import ContextView, build_context as domain_build_context
from app.harness.context_policy import seed_context_policy, select_action_class
from app.harness.memory_retriever import retrieve
from app.harness.model_client import ModelClient, ModelOutputError
from app.harness.jev_scorer import OpenRouterJevScorer
from app.harness.narrator import fallback_narration, narrate as domain_narrate
from app.services.stubs import EngineResolution, NarrationResult, Proposal, WorldView

_logger = logging.getLogger("many_lives.harness")


def _event_from_document(document: Mapping[str, Any]) -> Event:
    """Validate the domain fields of a persisted event document."""
    return Event.model_validate(
        {field: document[field] for field in Event.model_fields if field in document}
    )


def _domain_events(events: list[dict[str, Any]] | None) -> list[Event] | None:
    """Map committed event documents onto domain Events (§10.6).

    The seam carries plain documents so that `app/services/stubs.py` stays free
    of domain imports. A document the current Event model cannot accept is
    skipped rather than raised: narration runs *after* commit, so a shape
    mismatch must never turn a committed turn into an error (§7.1.7).
    """
    if not events:
        return None
    mapped: list[Event] = []
    for document in events:
        try:
            mapped.append(_event_from_document(document))
        except Exception as exc:  # noqa: BLE001 - post-commit: degrade, never fail
            _logger.warning(
                "dropping event %s from narrator context: %s",
                document.get("event_id", "<no id>"),
                exc,
            )
    if events and not mapped:
        _logger.error(
            "no committed event survived mapping; the narrator will fall back "
            "to the snapshot alone (%d document(s) rejected)",
            len(events),
        )
    return mapped or None


class _WorldViewContext(ContextView):
    """Context read model available from C's current integration seam.

    A durable engine can additionally supply SQLite through ``ProductionHarness``
    so recent exact events and vector memories are fetched campaign-scoped.
    """

    def __init__(self, world_view: WorldView, db: Any | None) -> None:
        self.world_view = world_view
        self.db = db

    def current_state(
        self,
        *,
        campaign: Mapping[str, Any],
        player: Mapping[str, Any],
        room: Mapping[str, Any],
        target_ids: Sequence[str],
    ) -> Mapping[str, Any]:
        cell = self.world_view.visible_cell
        characters = [
            {
                "entity_id": character.id,
                "name": character.name,
                "entity_type": character.entity_type,
                "status": character.status,
                "disposition": character.disposition,
                "physical_conditions": character.physical_conditions,
                "mental_conditions": character.mental_conditions,
            }
            for character in cell.characters
        ]
        features = [
            {
                "entity_id": feature.id,
                "name": feature.name,
                "entity_type": "FEATURE",
                "state": feature.state,
            }
            for feature in cell.features
        ]
        items = [
            {"entity_id": item.id, "name": item.name, "entity_type": "ITEM", "where": item.where}
            for item in cell.items
        ]
        entities = [*characters, *features, *items]
        target = next(
            (entity for entity in entities if entity["entity_id"] in target_ids),
            {},
        )
        return {
            "player_state": self.world_view.player.model_dump(),
            "player_inventory": [],
            "current_cell": cell.model_dump(),
            "visible_entities": [*characters, *features, *items],
            "target_state": target,
            "npc_disposition": {
                "entity_id": target.get("entity_id"),
                "disposition": target.get("disposition"),
            }
            if target
            else {},
            "npc_knowledge": [],
            "known_map": {},
            "active_quests": [],
        }

    def recent_events(
        self,
        *,
        campaign_id: str,
        player_id: str,
        room_id: str,
        target_ids: Sequence[str],
        limit: int,
    ) -> Sequence[Any]:
        if self.db is None or not limit:
            return []
        documents = self.db.events.find({"campaign_id": campaign_id}).sort(
            [("turn_sequence", -1), ("event_index", -1)]
        ).limit(limit)
        return [_event_from_document(document) for document in reversed(list(documents))]


def _wire_manifest(manifest, budget_tokens: int) -> WireContextManifest:
    return WireContextManifest(
        policy_version=manifest.policy_version,
        components=manifest.components,
        entity_ids=manifest.entity_ids,
        event_ids=manifest.event_ids,
        memories=[MemoryRef(id=memory.id, score=memory.score) for memory in manifest.memories],
        estimated_tokens=manifest.estimated_tokens,
        budget_tokens=budget_tokens,
        notes=manifest.flags,
    )


def _model_call(record) -> ModelCall:
    return ModelCall(
        role=record.role.value,
        model=record.model,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        latency_ms=record.latency_ms,
        attempts=record.attempts,
        schema_valid=record.schema_valid,
    )


def _snapshot(view: WorldView) -> CellSnapshot:
    cell = view.visible_cell
    return CellSnapshot(
        cell_id=cell.cell_id,
        name=cell.name,
        description=cell.description,
        features=[
            SnapshotEntity(
                entity_id=feature.id,
                name=feature.name,
                kind="FEATURE",
                state=feature.state,
            )
            for feature in cell.features
        ],
        characters=[
            SnapshotEntity(
                entity_id=character.id,
                name=character.name,
                kind="CHARACTER",
                status=character.status,
                disposition=character.disposition,
                physical_conditions=character.physical_conditions,
                mental_conditions=character.mental_conditions,
            )
            for character in cell.characters
        ],
        items=[
            SnapshotEntity(
                entity_id=item.id,
                name=item.name,
                kind="ITEM",
                location=item.where,
            )
            for item in cell.items
        ],
    )


def _social_context(view: WorldView, events: list[Event] | None) -> SocialContext | None:
    """Build the narrator's NPC context from this turn's committed events (§10.6)."""
    npc_id: str | None = None
    utterances: list[str] = []
    revealed: str | None = None
    for event in events or ():
        payload = event.payload
        if event.type in {EventType.DIALOGUE, EventType.CHECK_RESOLVED} and payload.get("npc_id"):
            npc_id = npc_id or str(payload["npc_id"])
            if payload.get("utterance"):
                utterances.append(str(payload["utterance"]))
        elif event.type == EventType.FACT_REVEALED:
            revealed = event.summary
    if npc_id is None:
        return None
    npc = next((c for c in view.visible_cell.characters if c.id == npc_id), None)
    return SocialContext(
        npc_id=npc_id,
        persona=npc.name if npc else None,
        disposition=npc.disposition if npc else None,
        revealed_fact=revealed,
        recent_dialogue=[f"Player: {text}" for text in utterances],
    )


def _player_summary(view: WorldView) -> PlayerSummary:
    player = view.player
    return PlayerSummary(
        player_id=view.player_id,
        hp=player.hp,
        max_hp=player.max_hp,
        mp=player.mp,
        max_mp=player.max_mp,
        level=player.level,
        physical_conditions=player.physical_conditions,
        mental_conditions=player.mental_conditions,
    )


class ProductionHarness:
    """B's real implementation of C's ``HarnessPort`` protocol."""

    def __init__(self, *, client: ModelClient, db: Any | None = None) -> None:
        self.client = client
        self.db = db
        self.policy = seed_context_policy()
        self.candidate_generator = ModelCandidateGenerator(client)
        self.jev_scorer = OpenRouterJevScorer()

    def context_budget(self) -> int:
        return int(self.policy.budget["max_context_tokens"])

    def classify(self, text: str, view: WorldView) -> str:
        visible_entities = [
            {"name": character.name, "entity_type": character.entity_type, "disposition": character.disposition}
            for character in view.visible_cell.characters
        ]
        return select_action_class(text, {"visible_entities": visible_entities}).value

    def build_context(
        self, view: WorldView, action_class: str, action_text: str | None
    ) -> tuple[str, WireContextManifest, int | None]:
        context_view = _WorldViewContext(view, self.db)
        vector_search_ms: int | None = None
        target_ids = [
            entity.id
            for entity in [
                *view.visible_cell.characters,
                *view.visible_cell.features,
                *view.visible_cell.items,
            ]
        ]

        def retriever(query):
            nonlocal vector_search_ms
            if self.db is None:
                from app.domain.types import RetrievalResult

                return RetrievalResult(flags=["VECTOR_UNAVAILABLE"])
            started = monotonic()
            result = retrieve(
                db=self.db,
                client=self.client,
                campaign_id=query.campaign_id,
                query_text=query.query_text,
                cfg=query.config,
                entity_ids=query.entity_ids,
                cell_id=query.cell_id,
                recent_event_ids=set(query.recent_event_ids),
            )
            vector_search_ms = round((monotonic() - started) * 1000)
            return result

        text, manifest = domain_build_context(
            role=Role.ADJUDICATOR,
            action_class=ActionClass(action_class),
            policy=self.policy,
            view=context_view,
            campaign={"campaign_id": view.campaign_id},
            player={"entity_id": view.player_id},
            room={"cell_id": view.visible_cell.cell_id},
            action_text=action_text,
            target_ids=target_ids,
            retriever=retriever,
        )
        return text, _wire_manifest(manifest, self.context_budget()), vector_search_ms

    def adjudicate(
        self, text: str, context_text: str, view: WorldView, action_class: str
    ) -> tuple[Proposal | None, list[ModelCall]]:
        known_ids = {
            view.player_id,
            *(character.id for character in view.visible_cell.characters),
            *(feature.id for feature in view.visible_cell.features),
            *(item.id for item in view.visible_cell.items),
        }
        try:
            result = domain_adjudicate(
                player_text=text,
                context_text=context_text,
                actor_id=view.player_id,
                known_ids=known_ids,
                client=self.client,
            )
        except ModelOutputError:
            # §7.1.3: invalid structured output after retries rejects the turn.
            _logger.warning("adjudicator output invalid after retries", exc_info=True)
            return None, []
        proposal = result.proposal
        params: dict[str, Any] = {}
        if proposal.check is not None:
            params = {
                "check_kind": proposal.check.kind.value,
                "suggested_difficulty": proposal.check.suggested_difficulty,
                "approach_modifier": proposal.check.approach_modifier,
            }
        return (
            Proposal(
                action_type=proposal.action_type.value,
                actor_id=proposal.actor_id,
                targets=proposal.targets,
                feasibility=proposal.feasibility.value,
                reason=proposal.reason,
                params=params,
                proposed_effects_on_success=[
                    effect.model_dump(mode="json")
                    for effect in proposal.proposed_effects_on_success
                ],
                proposed_effects_on_failure=[
                    effect.model_dump(mode="json")
                    for effect in proposal.proposed_effects_on_failure
                ],
                utterance=proposal.utterance,
            ),
            [_model_call(result.model_call)],
        )

    def narrate(
        self,
        view: WorldView,
        resolution: EngineResolution,
        kind: str,
        events: list[dict[str, Any]] | None = None,
    ) -> NarrationResult:
        domain_events = _domain_events(events)
        result, call = domain_narrate(
            events=domain_events,
            rejection_reason=resolution.reason if not resolution.accepted else None,
            snapshot=_snapshot(view),
            player_summary=_player_summary(view),
            social=_social_context(view, domain_events),
            context_text="Current deterministic state is authoritative.",
            client=self.client,
        )
        return NarrationResult(
            prose=result.prose,
            claims=[claim.model_dump(mode="json") for claim in result.claims],
            source="MODEL",
            model_call=_model_call(call),
        )

    def template_narration(
        self,
        view: WorldView,
        resolution: EngineResolution,
        kind: str,
        events: list[dict[str, Any]] | None = None,
    ) -> str:
        if not resolution.accepted and resolution.reason:
            return resolution.reason
        return fallback_narration(_domain_events(events) or [], _snapshot(view)).prose
