"""API request/response models (TDD §17.2-§17.3).

The wire contract between the browser client and the server. Distinct from the
domain types in ``app.domain.types``: those describe engine internals, these
describe JSON on the wire. All models use ``extra="forbid"`` (TDD §8.2) so an
unexpected field is a 422 rather than a silently ignored key.

NOTE for Developer A: ``app.domain.types`` also defines a ``TurnResult`` with a
different shape (current_cell_id / events / outcome_summary). This module's
``TurnResult`` is the §17.2 wire shape; the orchestrator maps engine -> wire.
Owned by C, reviewed by A and B.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Errors (§17.1)
# --------------------------------------------------------------------------


class ErrorBody(_Wire):
    code: str
    message: str


class ErrorResponse(_Wire):
    error: ErrorBody


# --------------------------------------------------------------------------
# Campaigns (§17.2)
# --------------------------------------------------------------------------


class CampaignSummary(_Wire):
    campaign_id: str
    status: str
    current_turn: int
    player_id: str
    player_name: str
    updated_at: str


class CreateCampaignRequest(_Wire):
    player_name: str = Field(min_length=1, max_length=40)
    seed: int | None = None


class CreateCampaignResponse(_Wire):
    """§17.2: `CampaignSummary` + initial `TurnResult` for spawn."""

    campaign: CampaignSummary
    initial: "TurnResult"


class ResumeRequest(_Wire):
    player_id: str = Field(min_length=1, max_length=64)


# --------------------------------------------------------------------------
# Turns (§17.2)
# --------------------------------------------------------------------------

# Player text is untrusted (§5.11, §22). Bounding the length here is the first
# trust-boundary check: oversized input is a 422, never a model call.
MAX_INPUT_CHARS = 500


class TurnRequest(_Wire):
    turn_id: str = Field(min_length=8, max_length=64)
    player_id: str = Field(min_length=1, max_length=64)
    input: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)


class Roll(_Wire):
    purpose: str
    sides: int
    value: int


class Outcome(_Wire):
    summary: str
    events: list[str] = Field(default_factory=list)
    rolls: list[Roll] = Field(default_factory=list)


class PlayerState(_Wire):
    """The compact player block embedded in every TurnResult."""

    hp: int
    max_hp: int
    mp: int
    max_mp: int
    level: int
    xp: int
    pending_level_ups: int = 0
    cell_id: str


class VisibleFeature(_Wire):
    id: str
    name: str
    state: dict[str, str] = Field(default_factory=dict)


class VisibleCharacter(_Wire):
    id: str
    name: str
    status: str
    disposition: str | None = None


class VisibleItem(_Wire):
    id: str
    name: str
    where: str


class VisibleCell(_Wire):
    cell_id: str
    name: str
    description: str = ""
    exits: list[str] = Field(default_factory=list)
    features: list[VisibleFeature] = Field(default_factory=list)
    characters: list[VisibleCharacter] = Field(default_factory=list)
    items: list[VisibleItem] = Field(default_factory=list)


class TurnResult(_Wire):
    turn_id: str
    turn_sequence: int
    status: str
    accepted: bool
    reason: str | None = None
    narration: str
    narration_source: Literal["MODEL", "TEMPLATE"] = "MODEL"
    outcome: Outcome
    player: PlayerState
    visible_cell: VisibleCell
    campaign_status: str
    debug_available: bool = False


class ResumeResult(_Wire):
    """§7.4 / §17.2: state + map + narration + manifest."""

    campaign: CampaignSummary
    player: PlayerState
    visible_cell: VisibleCell
    map: "MapResponse"
    narration: str
    narration_source: Literal["MODEL", "TEMPLATE"] = "MODEL"
    manifest: "ContextManifest | None" = None


# --------------------------------------------------------------------------
# Map (§17.3)
# --------------------------------------------------------------------------


class MapCell(_Wire):
    cell_id: str
    x: int
    y: int
    state: Literal["DISCOVERED", "RUMORED"]
    # Exits are returned only for DISCOVERED cells (§17.3).
    exits: list[str] = Field(default_factory=list)
    name: str | None = None
    boss: bool = False


class MapResponse(_Wire):
    width: int
    height: int
    player_cell: str
    cells: list[MapCell] = Field(default_factory=list)
    rumored: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Player sheet (§17.2 `GET /player`)
# --------------------------------------------------------------------------


class Stats(_Wire):
    attack: int
    defense: int
    speed: int
    dodge_pct: int
    skill: int


class InventoryItem(_Wire):
    id: str
    name: str
    quantity: int = 1
    slot: str | None = None
    stackable: bool = False


class PlayerSheet(_Wire):
    player_id: str
    name: str
    hp: int
    max_hp: int
    mp: int
    max_mp: int
    level: int
    xp: int
    pending_level_ups: int = 0
    cell_id: str
    status: str
    stats: Stats
    carried: list[InventoryItem] = Field(default_factory=list)
    weapon: InventoryItem | None = None
    armor: InventoryItem | None = None
    carried_slots: int = 6
    keys_held: int = 0
    keys_required: int = 3


# --------------------------------------------------------------------------
# Debug / context inspector (§9.7, §17.2)
# --------------------------------------------------------------------------


class MemoryRef(_Wire):
    id: str
    score: float
    text: str | None = None


class ContextManifest(_Wire):
    policy_version: int
    components: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    memories: list[MemoryRef] = Field(default_factory=list)
    estimated_tokens: int = 0
    notes: list[str] = Field(default_factory=list)


class ModelCall(_Wire):
    role: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    attempts: int = 1
    schema_valid: bool = True


class ClaimCheck(_Wire):
    entity_id: str
    attribute: str
    value: Any = None
    verdict: str = "UNCHECKED"


class Verification(_Wire):
    claims_checked: int = 0
    contradictions: int = 0
    unknown_entities: int = 0
    absent_entity_mentions: int = 0


class DebugContext(_Wire):
    campaign_id: str
    turn_id: str
    kind: str
    status: str
    path: str
    action_class: str | None = None
    input: str | None = None
    context_manifest: ContextManifest | None = None
    proposal: dict[str, Any] | None = None
    accepted_effect_types: list[str] = Field(default_factory=list)
    rejected_effects: list[dict[str, Any]] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    claims: list[ClaimCheck] = Field(default_factory=list)
    verification: Verification | None = None
    invariants: dict[str, Any] | None = None
    model_calls: list[ModelCall] = Field(default_factory=list)
    vector_search_ms: int | None = None
    created_at: str | None = None
    committed_at: str | None = None
    narrated_at: str | None = None


CreateCampaignResponse.model_rebuild()
ResumeResult.model_rebuild()
