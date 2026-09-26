"""Core domain types (TDD §8 & §9).

All models use ConfigDict(extra="forbid"); enums are StrEnum.
"""

from enum import StrEnum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class ActionType(StrEnum):
    MOVE = "MOVE"
    ATTACK = "ATTACK"
    FLEE = "FLEE"
    TAKE_ITEM = "TAKE_ITEM"
    DROP_ITEM = "DROP_ITEM"
    EQUIP = "EQUIP"
    UNEQUIP = "UNEQUIP"
    USE_ITEM = "USE_ITEM"
    CAST = "CAST"
    SEARCH = "SEARCH"
    TALK = "TALK"
    PERSUADE = "PERSUADE"
    DECEIVE = "DECEIVE"
    INTIMIDATE = "INTIMIDATE"
    STEAL = "STEAL"
    INTERACT = "INTERACT"
    CREATIVE_INTERACTION = "CREATIVE_INTERACTION"
    LEVEL_UP = "LEVEL_UP"
    LOOK = "LOOK"
    WAIT = "WAIT"


class EntityType(StrEnum):
    PLAYER = "PLAYER"
    NPC = "NPC"
    ENEMY = "ENEMY"
    BOSS = "BOSS"
    ITEM = "ITEM"


class LocationKind(StrEnum):
    CELL = "CELL"
    INVENTORY = "INVENTORY"
    EQUIPPED = "EQUIPPED"
    CONTAINER = "CONTAINER"
    RESERVED = "RESERVED"
    NONE = "NONE"


class CharacterStatus(StrEnum):
    ALIVE = "ALIVE"
    INCAPACITATED = "INCAPACITATED"
    DEAD = "DEAD"


class EventType(StrEnum):
    CAMPAIGN_CREATED = "CAMPAIGN_CREATED"
    PLAYER_SPAWNED = "PLAYER_SPAWNED"
    CELL_GENERATED = "CELL_GENERATED"
    PLAYER_MOVED = "PLAYER_MOVED"
    CELL_DISCOVERED = "CELL_DISCOVERED"
    HOSTILE_ENCOUNTERED = "HOSTILE_ENCOUNTERED"
    ATTACK_RESOLVED = "ATTACK_RESOLVED"
    ENTITY_DIED = "ENTITY_DIED"
    ITEM_TRANSFERRED = "ITEM_TRANSFERRED"
    ITEM_DROPPED = "ITEM_DROPPED"
    ITEM_EQUIPPED = "ITEM_EQUIPPED"
    ITEM_UNEQUIPPED = "ITEM_UNEQUIPPED"
    ITEM_CONSUMED = "ITEM_CONSUMED"
    CHECK_RESOLVED = "CHECK_RESOLVED"
    FEATURE_STATE_CHANGED = "FEATURE_STATE_CHANGED"
    FEATURE_CREATED = "FEATURE_CREATED"
    DISPOSITION_CHANGED = "DISPOSITION_CHANGED"
    DIALOGUE = "DIALOGUE"
    FACT_REVEALED = "FACT_REVEALED"
    CELL_RUMORED = "CELL_RUMORED"
    PLAYER_DIED = "PLAYER_DIED"
    PLAYER_RESPAWNED = "PLAYER_RESPAWNED"
    XP_GAINED = "XP_GAINED"
    LEVEL_UP = "LEVEL_UP"
    SPELL_CAST = "SPELL_CAST"
    KEYS_SUBMITTED = "KEYS_SUBMITTED"
    BOSS_DOOR_UNLOCKED = "BOSS_DOOR_UNLOCKED"
    TREASURE_CLAIMED = "TREASURE_CLAIMED"
    QUEST_ISSUED = "QUEST_ISSUED"
    QUEST_STATE_CHANGED = "QUEST_STATE_CHANGED"


class MemoryStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    NOT_REQUIRED = "NOT_REQUIRED"


# Domain Models
class Location(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: LocationKind
    ref_id: str | None = None
    slot: str | None = None


class ActionIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_type: ActionType
    actor_id: str
    targets: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class Effect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    target_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    campaign_id: str
    event_id: str
    turn_sequence: int
    event_index: int
    turn_id: str
    type: EventType
    actor_id: str
    entity_ids: list[str] = Field(default_factory=list)
    cell_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str
    memory_status: MemoryStatus = MemoryStatus.NOT_REQUIRED
    memory_attempts: int = 0
    schema_version: int = 1


class TurnResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_id: str
    turn_sequence: int
    accepted: bool
    reason: str | None = None
    current_cell_id: str
    events: list[Event] = Field(default_factory=list)
    outcome_summary: str = ""
