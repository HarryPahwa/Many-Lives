"""Core domain and model-contract types (TDD §8 and §9).

This module is deliberately pure: it contains no persistence, harness, service,
API, or world imports. All Pydantic models reject unknown fields.
"""

from enum import StrEnum
from typing import Annotated, Any, Literal, TypeAlias, Union

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


class Feasibility(StrEnum):
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    REQUIRES_CHECK = "REQUIRES_CHECK"


class CheckKind(StrEnum):
    PERSUADE = "PERSUADE"
    DECEIVE = "DECEIVE"
    INTIMIDATE = "INTIMIDATE"
    SEARCH = "SEARCH"
    STEAL = "STEAL"
    SKILL = "SKILL"


class ClaimAttribute(StrEnum):
    STATUS = "status"
    DISPOSITION = "disposition"
    LOCATION = "location"
    ORIENTATION = "orientation"
    CONDITION = "condition"
    OPEN_STATE = "open_state"
    LOCK_STATE = "lock_state"
    LIGHT_STATE = "light_state"
    PRESENT = "present"


class DispositionState(StrEnum):
    HOSTILE = "HOSTILE"
    WARY = "WARY"
    NEUTRAL = "NEUTRAL"
    FRIENDLY = "FRIENDLY"


class DispositionDirection(StrEnum):
    WORSEN = "WORSEN"
    IMPROVE = "IMPROVE"


class FeatureProperty(StrEnum):
    FLAMMABLE = "flammable"
    BREAKABLE = "breakable"
    MOVABLE = "movable"
    HEAVY = "heavy"
    CONTAINER = "container"
    CONCEALING = "concealing"
    LIGHT_SOURCE = "light_source"


class FeatureStateKey(StrEnum):
    OPEN_STATE = "open_state"
    LOCK_STATE = "lock_state"
    CONDITION = "condition"
    ORIENTATION = "orientation"
    LIGHT_STATE = "light_state"


class FeatureState(BaseModel):
    """Closed persistent feature-state keys used by model-facing contracts."""

    model_config = ConfigDict(extra="forbid")
    open_state: str | None = None
    lock_state: str | None = None
    condition: str | None = None
    orientation: str | None = None
    light_state: str | None = None


class Archetype(StrEnum):
    EMPTY = "EMPTY"
    ENEMY = "ENEMY"
    NPC = "NPC"
    ITEM = "ITEM"
    ENEMY_WITH_ITEM = "ENEMY_WITH_ITEM"
    NPC_WITH_ITEM = "NPC_WITH_ITEM"
    ENEMY_AND_NPC = "ENEMY_AND_NPC"
    BOSS = "BOSS"


class CampaignStatus(StrEnum):
    ACTIVE = "ACTIVE"
    WON = "WON"
    ABANDONED = "ABANDONED"


class GenerationStatus(StrEnum):
    UNGENERATED = "UNGENERATED"
    GENERATING = "GENERATING"
    PLANNED = "PLANNED"
    DRESSED = "DRESSED"
    VALIDATED = "VALIDATED"
    GENERATED = "GENERATED"


class ItemSubtype(StrEnum):
    KEY = "KEY"
    TREASURE = "TREASURE"
    WEAPON = "WEAPON"
    ARMOR = "ARMOR"
    MANA_POTION = "MANA_POTION"
    SPELLBOOK = "SPELLBOOK"
    TRINKET = "TRINKET"
    QUEST_ITEM = "QUEST_ITEM"
    BODY_PART = "BODY_PART"
    IMPROVISED = "IMPROVISED"


class DangerTierLabel(StrEnum):
    QUIET = "quiet"
    UNEASY = "uneasy"
    DANGEROUS = "dangerous"
    DEADLY = "deadly"
    LAIR = "lair"


class RoomEntityRole(StrEnum):
    ENEMY = "ENEMY"
    NPC = "NPC"
    BOSS = "BOSS"


class ItemSlotPlacement(StrEnum):
    FLOOR = "FLOOR"
    CONTAINER = "CONTAINER"
    HIDDEN = "HIDDEN"
    HELD = "HELD"
    GUARDED = "GUARDED"


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
    params: dict[str, str | int] = Field(default_factory=dict)


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: CheckKind
    suggested_difficulty: int
    approach_modifier: int


class TransferItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["TRANSFER_ITEM"]
    item_id: str
    from_loc: Location
    to_loc: Location
    quantity: int = Field(default=1, ge=1)


class ConsumeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["CONSUME_ITEM"]
    item_id: str
    quantity: int = Field(default=1, ge=1)


class SetFeatureState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["SET_FEATURE_STATE"]
    feature_id: str
    key: FeatureStateKey
    value: str


class CreateFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["CREATE_FEATURE"]
    kind: str
    name: str = Field(max_length=40)
    properties: list[FeatureProperty]
    state: FeatureState


class SetDisposition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["SET_DISPOSITION"]
    entity_id: str
    direction: DispositionDirection


class AdjustStat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ADJUST_STAT"]
    entity_id: str
    stat: Literal["hp"]
    delta: int = Field(ge=-3, le=0)


class MoveEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["MOVE_ENTITY"]
    entity_id: str
    to_cell: str


class SetStat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["SET_STAT"]
    entity_id: str
    stat: str
    value: int


class CreateEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["CREATE_ENTITY"]
    entity_type: EntityType
    payload: dict[str, Any]


class AddNpcKnowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ADD_NPC_KNOWLEDGE"]
    entity_id: str
    fact_id: str


class SetQuestState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["SET_QUEST_STATE"]
    quest_id: str
    status: str


class SetBossDoorState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["SET_BOSS_DOOR_STATE"]
    unlocked: bool


class MarkCellRumored(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["MARK_CELL_RUMORED"]
    cell_id: str


class Noop(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["NOOP"]


Effect: TypeAlias = Annotated[
    Union[
        TransferItem,
        ConsumeItem,
        SetFeatureState,
        CreateFeature,
        SetDisposition,
        AdjustStat,
        MoveEntity,
        SetStat,
        CreateEntity,
        AddNpcKnowledge,
        SetQuestState,
        SetBossDoorState,
        MarkCellRumored,
        Noop,
    ],
    Field(discriminator="type"),
]


class ActionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_type: ActionType
    actor_id: str
    targets: list[str]
    feasibility: Feasibility
    reason: str = Field(max_length=300)
    check: Check | None
    proposed_effects_on_success: list[Effect]
    proposed_effects_on_failure: list[Effect]
    utterance: str | None


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str
    attribute: ClaimAttribute
    value: str | bool


class NarrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prose: str
    claims: list[Claim]


class SnapshotEntity(BaseModel):
    """Read-only entity projection used by narration and verification."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    name: str
    kind: str
    status: str | None = None
    disposition: str | None = None
    location: str | None = None
    state: dict[str, str] = Field(default_factory=dict)


class CellSnapshot(BaseModel):
    """Post-resolution cell state. It is descriptive, never authoritative."""

    model_config = ConfigDict(extra="forbid")

    cell_id: str
    name: str
    description: str = ""
    features: list[SnapshotEntity] = Field(default_factory=list)
    characters: list[SnapshotEntity] = Field(default_factory=list)
    items: list[SnapshotEntity] = Field(default_factory=list)


class PlayerSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player_id: str
    hp: int
    max_hp: int
    mp: int
    max_mp: int
    level: int
    equipment: list[str] = Field(default_factory=list)


class SocialContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str
    persona: str | None = None
    disposition: str | None = None
    allowed_facts: list[str] = Field(default_factory=list)
    revealed_fact: str | None = None
    recent_dialogue: list[str] = Field(default_factory=list)
    relationship_memories: list[str] = Field(default_factory=list)


class StaticEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    materials: list[str] = Field(min_length=1, max_length=3)
    lighting: str
    smell: str
    architectural_notes: str


class FeatureDressing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot_id: str | None
    kind: str
    name: str = Field(max_length=40)
    properties: list[FeatureProperty]
    initial_state: FeatureState


class EntityDressing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot_id: str
    name: str = Field(max_length=40)
    description: str = Field(max_length=200)
    persona: str | None
    traits: list[str]


class ItemDressing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot_id: str
    name: str = Field(max_length=40)
    description: str = Field(max_length=200)


class RoomDressing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    room_name: str = Field(max_length=40)
    static_environment: StaticEnvironment
    features: list[FeatureDressing] = Field(min_length=2, max_length=5)
    entities: list[EntityDressing]
    items: list[ItemDressing]


class EntitySlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot_id: str
    role: RoomEntityRole


class ItemSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot_id: str
    subtype_hint: ItemSubtype | None = None
    placement: ItemSlotPlacement
    container_slot_id: str | None = None
    holder_slot_id: str | None = None
    guard_slot_id: str | None = None


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: str
    type: Literal["CELL_HINT"]
    subject_cell_id: str
    hint: str


class RoomPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cell_key: str
    archetype: Archetype
    tier: int = Field(ge=1, le=5)
    entity_slots: list[EntitySlot]
    item_slots: list[ItemSlot]
    feature_range: tuple[int, int]
    knowledge_facts: list[Fact]


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_id: str
    player_id: str
    input: str


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


class EngineTurnResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_id: str
    turn_sequence: int
    accepted: bool
    reason: str | None = None
    current_cell_id: str
    events: list[Event] = Field(default_factory=list)
    outcome_summary: str = ""


# Harness-only contracts extend the engine's canonical types above.  They do
# not replace the engine's room, event, action, or result shapes.
class DomainModel(BaseModel):
    """Base model for strict harness contracts."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ActionClass(StrEnum):
    MOVE = "MOVE"
    COMBAT = "COMBAT"
    ITEM = "ITEM"
    SEARCH = "SEARCH"
    CREATIVE = "CREATIVE"
    SOCIAL = "SOCIAL"
    RESUME = "RESUME"
    NARRATION_DEFAULT = "NARRATION_DEFAULT"


class Role(StrEnum):
    DRESSER = "DRESSER"
    ADJUDICATOR = "ADJUDICATOR"
    NARRATOR = "NARRATOR"
    VERIFIER = "VERIFIER"
    MEMORY_SUMMARIZER = "MEMORY_SUMMARIZER"


class MemoryType(StrEnum):
    RELATIONSHIP = "RELATIONSHIP"
    DIALOGUE = "DIALOGUE"
    COMBAT = "COMBAT"
    DISCOVERY = "DISCOVERY"
    ITEM = "ITEM"
    ENVIRONMENT = "ENVIRONMENT"
    QUEST = "QUEST"
    BOSS = "BOSS"


class PolicyStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CANDIDATE = "CANDIDATE"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


class PolicyCreator(StrEnum):
    HUMAN = "HUMAN"
    OPTIMIZER = "OPTIMIZER"


class VectorMemoryConfig(DomainModel):
    enabled: bool
    top_k: int = 0
    memory_types: list[MemoryType] | None = None
    entity_filter: bool = False
    cell_filter: bool = False


class RetrievalQuery(DomainModel):
    """The campaign-scoped input supplied to a memory retriever."""

    campaign_id: str
    query_text: str
    config: VectorMemoryConfig
    entity_ids: list[str] = Field(default_factory=list)
    cell_id: str | None = None
    recent_event_ids: list[str] = Field(default_factory=list)


class RetrievedMemory(DomainModel):
    """A retrieval projection of a persisted memory, including its search score."""

    memory_id: str = Field(alias="_id")
    campaign_id: str
    schema_version: int = 1
    memory_type: MemoryType
    entity_ids: list[str]
    cell_id: str | None
    source_event_ids: list[str]
    created_turn: int
    importance: float
    text: str
    embedding_model: str
    created_at: str
    score: float


class RetrievalResult(DomainModel):
    """Ordered memories and non-fatal retrieval status flags."""

    memories: list[RetrievedMemory] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class ClassRule(DomainModel):
    mandatory: list[str]
    conditional: list[str]
    recent_event_window: int
    vector_memory: VectorMemoryConfig


class ContextPolicy(DomainModel):
    policy_id: str = Field(alias="_id")
    version: int
    status: PolicyStatus
    parent_version: int | None
    created_by: PolicyCreator
    rules: dict[ActionClass, ClassRule]
    budget: dict[str, int]
    promotion_metrics: dict[str, float] | None
    created_at: str


class MemoryReference(DomainModel):
    id: str
    score: float


class ContextManifest(DomainModel):
    policy_version: int
    action_class: ActionClass
    components: list[str]
    entity_ids: list[str]
    event_ids: list[str]
    memories: list[MemoryReference]
    estimated_tokens: int
    flags: list[str]


class ModelCallRecord(DomainModel):
    role: Role
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    attempts: int
    schema_valid: bool


class Verification(DomainModel):
    claims_checked: int = 0
    contradictions: int = 0
    unknown_entities: int = 0
    absent_entity_mentions: int = 0
