"""Generic state mutation primitives (TDD §8, §13, §14).

Pure domain models representing atomic state modifications and candidate bundles.
"""

from typing import Annotated, Any, Literal, TypeAlias, Union
from pydantic import BaseModel, ConfigDict, Field

from app.domain.types import Event, EventType, LocationKind


class MutateAttribute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["MUTATE_ATTRIBUTE"] = "MUTATE_ATTRIBUTE"
    target_id: str
    path: str  # e.g., "stats.hp", "status", "disposition", "light_state", "physical_conditions", "mental_conditions"
    value: Any
    op: Literal["SET", "ADD", "REMOVE"] = "SET"


class TransferEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["TRANSFER_ENTITY"] = "TRANSFER_ENTITY"
    entity_id: str
    from_ref: str
    to_ref: str
    location_kind: LocationKind


class MoveEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["MOVE_ENTITY"] = "MOVE_ENTITY"
    entity_id: str
    target_cell_id: str


class AppendEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["APPEND_EVENT"] = "APPEND_EVENT"
    event_type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str


class ApplyDocumentMutation(BaseModel):
    """Application-owned canonical patch produced by deterministic rules."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["APPLY_DOCUMENT_MUTATION"] = "APPLY_DOCUMENT_MUTATION"
    collection: Literal["campaigns", "cells", "entities"]
    document_id: str
    expected_version: int
    set_fields: dict[str, Any] = Field(default_factory=dict)
    inc_fields: dict[str, int] = Field(default_factory=dict)
    add_to_set_fields: dict[str, Any] = Field(default_factory=dict)


class InsertDocument(BaseModel):
    """Application-owned canonical insert produced by deterministic rules."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["INSERT_DOCUMENT"] = "INSERT_DOCUMENT"
    collection: Literal["entities"]
    document: dict[str, Any]


class AppendCanonicalEvent(BaseModel):
    """Lossless deterministic event; IDs and ordering came from the rules engine."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["APPEND_CANONICAL_EVENT"] = "APPEND_CANONICAL_EVENT"
    event: Event


class ExecutionMetadata(BaseModel):
    """Commit metadata owned by application code, never by model proposals."""

    model_config = ConfigDict(extra="forbid")

    expected_turn: int | None = None
    expected_campaign_version: int | None = None
    turn_id: str | None = None
    current_cell_id: str | None = None
    touched_entity_ids: list[str] = Field(default_factory=list)
    touched_cell_ids: list[str] = Field(default_factory=list)
    rejected_effects: list[dict[str, Any]] = Field(default_factory=list)


StateMutation: TypeAlias = Annotated[
    Union[
        MutateAttribute,
        TransferEntity,
        MoveEntity,
        AppendEvent,
        ApplyDocumentMutation,
        InsertDocument,
        AppendCanonicalEvent,
    ],
    Field(discriminator="kind"),
]


class MutationBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bundle_id: str
    action_description: str
    rationale: str
    draft_narration: str
    origin: Literal["MODEL", "DETERMINISTIC"] = "MODEL"
    execution: ExecutionMetadata | None = None
    mutations: list[StateMutation] = Field(default_factory=list)
