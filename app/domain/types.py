"""Core domain types (TDD §8).

IDs, StrEnums, and Pydantic v2 models: ActionType, ActionIntent,
ActionProposal, Effect (discriminated union), Event/EventDraft, RoomPlan,
RoomDressing, RoomSpec, NarrationResult, Claim, ContextPolicy, Resolution,
TurnRequest, TurnResult.

All models use ConfigDict(extra="forbid"); enums are StrEnum.
Scaffold only — no definitions yet.
"""
