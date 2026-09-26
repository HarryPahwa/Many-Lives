"""Adapter from B's domain harness to C's integration seam.

This module is deliberately in ``services``: it composes the API-facing seam
with pure harness functions without making ``app.harness`` depend on services.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from time import monotonic
from typing import Any

from app.api.schemas import ContextManifest as WireContextManifest
from app.api.schemas import MemoryRef, ModelCall
from app.domain.types import (
    ActionClass,
    CellSnapshot,
    PlayerSummary,
    Role,
    SnapshotEntity,
)
from app.harness.adjudicator import adjudicate as domain_adjudicate
from app.harness.context_builder import ContextView, build_context as domain_build_context
from app.harness.context_policy import seed_context_policy, select_action_class
from app.harness.memory_retriever import retrieve
from app.harness.model_client import ModelClient
from app.harness.narrator import fallback_narration, narrate as domain_narrate
from app.services.stubs import EngineResolution, NarrationResult, Proposal, WorldView


class _WorldViewContext(ContextView):
    """Context read model available from C's current integration seam.

    A durable engine can additionally supply Mongo through ``ProductionHarness``
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
                "entity_type": "NPC",
                "status": character.status,
                "disposition": character.disposition,
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
        from app.domain.types import Event

        documents = self.db.events.find({"campaign_id": campaign_id}).sort(
            [("turn_sequence", -1), ("event_index", -1)]
        ).limit(limit)
        return [Event.model_validate(document) for document in reversed(list(documents))]


def _wire_manifest(manifest) -> WireContextManifest:
    return WireContextManifest(
        policy_version=manifest.policy_version,
        components=manifest.components,
        entity_ids=manifest.entity_ids,
        event_ids=manifest.event_ids,
        memories=[MemoryRef(id=memory.id, score=memory.score) for memory in manifest.memories],
        estimated_tokens=manifest.estimated_tokens,
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


def _player_summary(view: WorldView) -> PlayerSummary:
    player = view.player
    return PlayerSummary(
        player_id=view.player_id,
        hp=player.hp,
        max_hp=player.max_hp,
        mp=player.mp,
        max_mp=player.max_mp,
        level=player.level,
    )


class ProductionHarness:
    """B's real implementation of C's ``HarnessPort`` protocol."""

    def __init__(self, *, client: ModelClient, db: Any | None = None) -> None:
        self.client = client
        self.db = db
        self.policy = seed_context_policy()

    def classify(self, text: str, view: WorldView) -> str:
        visible_entities = [
            {"name": character.name, "entity_type": "NPC", "disposition": character.disposition}
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
        return text, _wire_manifest(manifest), vector_search_ms

    def adjudicate(
        self, text: str, context_text: str, view: WorldView, action_class: str
    ) -> tuple[Proposal | None, list[ModelCall]]:
        known_ids = {
            view.player_id,
            *(character.id for character in view.visible_cell.characters),
            *(feature.id for feature in view.visible_cell.features),
            *(item.id for item in view.visible_cell.items),
        }
        result = domain_adjudicate(
            player_text=text,
            context_text=context_text,
            actor_id=view.player_id,
            known_ids=known_ids,
            client=self.client,
        )
        proposal = result.proposal
        return (
            Proposal(
                action_type=proposal.action_type.value,
                actor_id=proposal.actor_id,
                targets=proposal.targets,
                feasibility=proposal.feasibility.value,
                reason=proposal.reason,
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

    def narrate(self, view: WorldView, resolution: EngineResolution, kind: str) -> NarrationResult:
        result, call = domain_narrate(
            events=None,
            rejection_reason=resolution.reason if not resolution.accepted else None,
            snapshot=_snapshot(view),
            player_summary=_player_summary(view),
            social=None,
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
        self, view: WorldView, resolution: EngineResolution, kind: str
    ) -> str:
        if not resolution.accepted and resolution.reason:
            return resolution.reason
        return fallback_narration([], _snapshot(view)).prose
