"""Typed semantic outcome generation for the optional two-step JEV pipeline."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, Protocol, TypeAlias, Union

from pydantic import Field

from app.domain.types import ActionClass, DomainModel, EventType, Role
from app.harness.model_client import ModelClient, StructuredResult


class DialogueConsequence(DomainModel):
    kind: Literal["DIALOGUE"] = "DIALOGUE"
    npc_id: str
    utterance: str | None
    dialogue_intent: str
    npc_reaction: str


class CombatConsequence(DomainModel):
    kind: Literal["COMBAT"] = "COMBAT"
    attacker_id: str
    target_id: str
    outcome: str
    severity: Literal["NONE", "LIGHT", "MODERATE", "HEAVY"]


class ConditionConsequence(DomainModel):
    kind: Literal["CONDITION"] = "CONDITION"
    target_id: str
    path: Literal["physical_conditions", "mental_conditions"]
    condition: str
    op: Literal["ADD", "REMOVE"]


class TransferConsequence(DomainModel):
    kind: Literal["TRANSFER"] = "TRANSFER"
    entity_id: str
    from_ref: str
    to_ref: str
    location_kind: Literal["CELL", "INVENTORY", "EQUIPPED", "CONTAINER", "NONE"]


class MovementConsequence(DomainModel):
    kind: Literal["MOVEMENT"] = "MOVEMENT"
    entity_id: str
    target_cell_id: str


class StateConsequence(DomainModel):
    kind: Literal["STATE"] = "STATE"
    target_id: str
    path: Literal["status", "disposition", "light_state"]
    value: str


OutcomeConsequence: TypeAlias = Annotated[
    Union[
        DialogueConsequence,
        CombatConsequence,
        ConditionConsequence,
        TransferConsequence,
        MovementConsequence,
        StateConsequence,
    ],
    Field(discriminator="kind"),
]


class CandidateOutcome(DomainModel):
    bundle_id: str
    action_description: str
    rationale: str
    actor_id: str
    action_class: ActionClass
    target_ids: list[str]
    consequences: list[OutcomeConsequence] = Field(default_factory=list)


class CandidateOutcomeSet(DomainModel):
    candidates: list[CandidateOutcome] = Field(min_length=1)


OUTCOME_SYSTEM_PROMPT = """You generate distinct semantic outcomes for one player action.
Every candidate MUST preserve the supplied expected_action_class, actor_id, and every
required_target_id. Vary only what happens: success, partial success, failure, defense,
counterattack, spoken response, or bounded environmental consequence. Never replace the
requested action with another action. Use only IDs in world_snapshot.

Dialogue consequences must name the addressed character and a response mode. An initiated
conversation requires a spoken response such as SPOKEN_GREETING, GUARDED_QUESTION,
VERBAL_REFUSAL, REPLY, or ANSWER; silence or gestures alone are invalid. Combat may contain
multiple ordered COMBAT consequences, including an enemy counterattack. Severity is semantic;
do not choose numeric damage. Return only JSON matching CandidateOutcomeSet."""


class OutcomeGenerator(Protocol):
    def generate_outcomes(
        self,
        player_input: str,
        world_snapshot: Mapping[str, Any],
        *,
        actor_id: str,
        action_class: ActionClass,
        required_target_ids: list[str],
        candidate_count: int,
        validation_feedback: list[str] | None = None,
    ) -> CandidateOutcomeSet: ...


class FakeOutcomeGenerator:
    def __init__(self, predefined_results: list[CandidateOutcomeSet] | None = None) -> None:
        self._predefined = list(predefined_results or [])
        self._calls: list[dict[str, Any]] = []

    def generate_outcomes(
        self,
        player_input: str,
        world_snapshot: Mapping[str, Any],
        *,
        actor_id: str,
        action_class: ActionClass,
        required_target_ids: list[str],
        candidate_count: int,
        validation_feedback: list[str] | None = None,
    ) -> CandidateOutcomeSet:
        self._calls.append(
            {
                "input": player_input,
                "world": world_snapshot,
                "actor_id": actor_id,
                "action_class": action_class,
                "required_target_ids": required_target_ids,
                "candidate_count": candidate_count,
                "validation_feedback": validation_feedback,
            }
        )
        if self._predefined:
            return self._predefined.pop(0)
        return CandidateOutcomeSet(
            candidates=[
                CandidateOutcome(
                    bundle_id=f"outcome_{index + 1}",
                    action_description=player_input,
                    rationale="Deterministic test outcome",
                    actor_id=actor_id,
                    action_class=action_class,
                    target_ids=list(required_target_ids),
                    consequences=[],
                )
                for index in range(candidate_count)
            ]
        )


class ModelOutcomeGenerator:
    def __init__(self, client: ModelClient) -> None:
        self.client = client
        self.last_result: StructuredResult[CandidateOutcomeSet] | None = None

    def generate_outcomes(
        self,
        player_input: str,
        world_snapshot: Mapping[str, Any],
        *,
        actor_id: str,
        action_class: ActionClass,
        required_target_ids: list[str],
        candidate_count: int,
        validation_feedback: list[str] | None = None,
    ) -> CandidateOutcomeSet:
        user = json.dumps(
            {
                "player_input": player_input,
                "world_snapshot": world_snapshot,
                "actor_id": actor_id,
                "expected_action_class": action_class.value,
                "required_target_ids": required_target_ids,
                "candidate_count": candidate_count,
                "validation_feedback": validation_feedback or [],
            },
            separators=(",", ":"),
            default=str,
        )
        self.last_result = self.client.structured(
            Role.ADJUDICATOR,
            OUTCOME_SYSTEM_PROMPT,
            user,
            CandidateOutcomeSet,
            temperature=0.7,
            max_output_tokens=3000,
            timeout_s=20.0,
        )
        return self.last_result.parsed


def mentioned_target_ids(player_input: str, world_snapshot: Mapping[str, Any]) -> list[str]:
    """Resolve explicit visible names without asking a model to establish grounding."""

    text = player_input.casefold()
    matches: list[str] = []
    entities: Sequence[Mapping[str, Any]] = [
        *world_snapshot.get("characters", []),
        *world_snapshot.get("items", []),
        *world_snapshot.get("container_items", []),
    ]
    for entity in entities:
        entity_id = entity.get("entity_id") or entity.get("id")
        name = entity.get("name")
        if isinstance(entity_id, str) and isinstance(name, str) and name.casefold() in text:
            matches.append(entity_id)
            continue
        if isinstance(entity_id, str) and isinstance(name, str):
            meaningful = [word for word in name.casefold().split() if len(word) > 3]
            if meaningful and any(word in text for word in meaningful):
                matches.append(entity_id)
    return list(dict.fromkeys(matches))


def validate_outcome(
    outcome: CandidateOutcome,
    world_snapshot: Mapping[str, Any],
    *,
    actor_id: str,
    action_class: ActionClass,
    required_target_ids: list[str],
) -> tuple[bool, str | None]:
    if outcome.actor_id != actor_id:
        return False, "OUTCOME_ACTOR_MISMATCH"
    if outcome.action_class != action_class:
        return False, "OUTCOME_ACTION_CLASS_MISMATCH"
    if not set(required_target_ids).issubset(outcome.target_ids):
        return False, "OUTCOME_REQUIRED_TARGET_MISSING"
    known_ids = {actor_id}
    for key in ("characters", "items", "container_items", "owned_items"):
        for entity in world_snapshot.get(key, []):
            entity_id = entity.get("entity_id") or entity.get("id")
            if entity_id:
                known_ids.add(entity_id)
    cell = world_snapshot.get("current_cell", {})
    cell_id = cell.get("cell_id") or cell.get("id")
    if cell_id:
        known_ids.add(cell_id)
    if any(target not in known_ids for target in outcome.target_ids):
        return False, "OUTCOME_UNKNOWN_TARGET"

    dialogue = [c for c in outcome.consequences if isinstance(c, DialogueConsequence)]
    if action_class == ActionClass.SOCIAL:
        if not dialogue:
            return False, "OUTCOME_DIALOGUE_RESPONSE_MISSING"
        if required_target_ids and not any(c.npc_id in required_target_ids for c in dialogue):
            return False, "OUTCOME_DIALOGUE_TARGET_MISMATCH"
        nonverbal = ("SILENCE", "NO_REACTION", "NOD", "STARE", "GESTURE_ONLY")
        if not any(
            c.npc_reaction.strip()
            and not any(marker in c.npc_reaction.upper() for marker in nonverbal)
            for c in dialogue
        ):
            return False, "OUTCOME_SPOKEN_RESPONSE_MISSING"

    for consequence in outcome.consequences:
        referenced = []
        for field in ("npc_id", "attacker_id", "target_id", "entity_id", "from_ref", "to_ref"):
            value = getattr(consequence, field, None)
            if isinstance(value, str):
                referenced.append(value)
        if any(value not in known_ids for value in referenced):
            return False, "OUTCOME_CONSEQUENCE_UNKNOWN_ENTITY"
    return True, None
