"""Generic state mutation primitives (TDD §8, §13, §14).

Pure domain models representing atomic state modifications and candidate bundles.
"""

from typing import Annotated, Any, Literal, TypeAlias, Union
from pydantic import BaseModel, ConfigDict, Field

from app.domain.types import EventType, LocationKind


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


StateMutation: TypeAlias = Annotated[
    Union[MutateAttribute, TransferEntity, MoveEntity, AppendEvent],
    Field(discriminator="kind"),
]


class MutationBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bundle_id: str
    action_description: str
    rationale: str
    draft_narration: str
    mutations: list[StateMutation] = Field(default_factory=list)
