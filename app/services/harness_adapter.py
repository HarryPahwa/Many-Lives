"""Adapter that lets the C turn seam use Developer B's typed narrator."""

from __future__ import annotations

from app.api.schemas import ModelCall
from app.domain.types import CellSnapshot, PlayerSummary, SnapshotEntity
from app.harness.model_client import ModelClient
from app.harness.narrator import fallback_narration, narrate
from app.services.stubs import EngineResolution, HarnessPort, NarrationResult, StubHarness, WorldView


def _snapshot(view: WorldView) -> CellSnapshot:
    cell = view.visible_cell
    return CellSnapshot(
        cell_id=cell.cell_id,
        name=cell.name,
        description=cell.description,
        features=[
            SnapshotEntity(entity_id=item.id, name=item.name, kind="FEATURE", state=item.state)
            for item in cell.features
        ],
        characters=[
            SnapshotEntity(
                entity_id=item.id,
                name=item.name,
                kind="CHARACTER",
                status=item.status,
                disposition=item.disposition,
                location=cell.cell_id,
            )
            for item in cell.characters
        ],
        items=[
            SnapshotEntity(entity_id=item.id, name=item.name, kind="ITEM", location=item.where)
            for item in cell.items
        ],
    )


def _player(view: WorldView) -> PlayerSummary:
    player = view.player
    return PlayerSummary(
        player_id=view.player_id,
        hp=player.hp,
        max_hp=player.max_hp,
        mp=player.mp,
        max_mp=player.max_mp,
        level=player.level,
    )


class RuntimeHarness:
    """Incremental adapter; unfinished B roles keep C's deterministic behavior."""

    def __init__(self, client: ModelClient, fallback: HarnessPort | None = None) -> None:
        self.client = client
        self.fallback = fallback or StubHarness()

    def classify(self, text: str) -> str:
        return self.fallback.classify(text)

    def build_context(self, view: WorldView, action_class: str):
        return self.fallback.build_context(view, action_class)

    def adjudicate(self, text: str, view: WorldView, action_class: str):
        return self.fallback.adjudicate(text, view, action_class)

    def narrate(self, view: WorldView, resolution: EngineResolution, kind: str) -> NarrationResult:
        result, call = narrate(
            events=None,
            rejection_reason=resolution.reason if not resolution.accepted else None,
            snapshot=_snapshot(view),
            player_summary=_player(view),
            social=None,
            context_text=resolution.outcome_summary,
            client=self.client,
        )
        return NarrationResult(
            prose=result.prose,
            claims=[claim.model_dump(mode="json") for claim in result.claims],
            source="MODEL",
            model_call=ModelCall(
                role=call.role.value,
                model=call.model,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                latency_ms=call.latency_ms,
                attempts=call.attempts,
                schema_valid=call.schema_valid,
            ),
        )

    def template_narration(self, view: WorldView, resolution: EngineResolution, kind: str) -> str:
        return fallback_narration([], _snapshot(view)).prose
