"""Compile one selected semantic outcome into an atomic mutation proposal bundle."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol

from app.domain.mutations import (
    AppendEvent,
    MoveEntity,
    MutateAttribute,
    MutationBundle,
    TransferEntity,
)
from app.domain.types import ActionClass, EventType, Role
from app.harness.candidate_generator import (
    ModelMutationBundle,
    canonicalize_model_mutations,
)
from app.harness.model_client import ModelClient, StructuredResult
from app.harness.outcome_generator import (
    CandidateOutcome,
    CombatConsequence,
    ConditionConsequence,
    DialogueConsequence,
    MovementConsequence,
    StateConsequence,
    TransferConsequence,
)


COMPILER_SYSTEM_PROMPT = """You compile one already-selected semantic outcome into one
complete atomic mutation bundle. Implement every consequence, preserve its actors and targets,
and add nothing unrelated. Multiple mutations are expected when one outcome has multiple
effects. Use only IDs and state in world_snapshot.

The ONLY mutation kind strings are exactly:
- APPEND_EVENT with event_type, typed payload, and summary. Never use EVENT.
- MUTATE_ATTRIBUTE with target_id, path, value, and op.
- TRANSFER_ENTITY with entity_id, from_ref, to_ref, and location_kind.
- MOVE_ENTITY with entity_id and target_cell_id.

For dialogue APPEND_EVENT payload is npc_id, utterance, dialogue_intent, npc_reaction.
For combat APPEND_EVENT payload is target_id, outcome, hp_delta. Combat hp_delta is negative
damage to the combat target and null for no damage. Do not also emit a stats.hp mutation for
that damage; application code derives it from hp_delta. Conditions use MUTATE_ATTRIBUTE with
ADD or REMOVE. Return only JSON matching ModelMutationBundle. You propose mutations;
application code validates and commits."""


class MutationCompiler(Protocol):
    def compile(
        self,
        outcome: CandidateOutcome,
        world_snapshot: Mapping[str, Any],
        validation_feedback: list[str] | None = None,
    ) -> MutationBundle: ...


class FakeMutationCompiler:
    def __init__(self, predefined_results: list[MutationBundle] | None = None) -> None:
        self._predefined = list(predefined_results or [])
        self._calls: list[dict[str, Any]] = []

    def compile(
        self,
        outcome: CandidateOutcome,
        world_snapshot: Mapping[str, Any],
        validation_feedback: list[str] | None = None,
    ) -> MutationBundle:
        self._calls.append(
            {
                "outcome": outcome,
                "world": world_snapshot,
                "validation_feedback": validation_feedback,
            }
        )
        if self._predefined:
            return self._predefined.pop(0)
        mutations: list[Any] = []
        severity_damage = {"NONE": None, "LIGHT": -2, "MODERATE": -5, "HEAVY": -8}
        for consequence in outcome.consequences:
            if isinstance(consequence, DialogueConsequence):
                mutations.append(
                    AppendEvent(
                        event_type=EventType.DIALOGUE,
                        payload=consequence.model_dump(exclude={"kind"}, exclude_none=True),
                        summary=outcome.action_description,
                    )
                )
            elif isinstance(consequence, CombatConsequence):
                damage = severity_damage[consequence.severity]
                payload = {"target_id": consequence.target_id, "outcome": consequence.outcome}
                if damage is not None:
                    payload["damage"] = abs(damage)
                mutations.append(
                    AppendEvent(
                        event_type=EventType.ATTACK_RESOLVED,
                        payload=payload,
                        summary=outcome.action_description,
                    )
                )
                if damage is not None:
                    mutations.append(
                        MutateAttribute(
                            target_id=consequence.target_id,
                            path="stats.hp",
                            value=damage,
                            op="ADD",
                        )
                    )
            elif isinstance(consequence, ConditionConsequence):
                mutations.append(
                    MutateAttribute(
                        target_id=consequence.target_id,
                        path=consequence.path,
                        value=consequence.condition,
                        op=consequence.op,
                    )
                )
            elif isinstance(consequence, TransferConsequence):
                mutations.append(TransferEntity(**consequence.model_dump(exclude={"kind"})))
            elif isinstance(consequence, MovementConsequence):
                mutations.append(MoveEntity(**consequence.model_dump(exclude={"kind"})))
            elif isinstance(consequence, StateConsequence):
                mutations.append(
                    MutateAttribute(
                        target_id=consequence.target_id,
                        path=consequence.path,
                        value=consequence.value,
                        op="SET",
                    )
                )
        return MutationBundle(
            bundle_id=outcome.bundle_id,
            action_description=outcome.action_description,
            rationale=outcome.rationale,
            draft_narration=outcome.action_description,
            mutations=mutations,
        )


class ModelMutationCompiler:
    def __init__(self, client: ModelClient) -> None:
        self.client = client
        self.last_result: StructuredResult[ModelMutationBundle] | None = None

    def compile(
        self,
        outcome: CandidateOutcome,
        world_snapshot: Mapping[str, Any],
        validation_feedback: list[str] | None = None,
    ) -> MutationBundle:
        self.last_result = self.client.structured(
            Role.ADJUDICATOR,
            COMPILER_SYSTEM_PROMPT,
            json.dumps(
                {
                    "selected_outcome": outcome.model_dump(mode="json"),
                    "world_snapshot": world_snapshot,
                    "validation_feedback": validation_feedback or [],
                },
                separators=(",", ":"),
                default=str,
            ),
            ModelMutationBundle,
            temperature=0.2,
            max_output_tokens=1800,
            timeout_s=20.0,
        )
        parsed = self.last_result.parsed
        return MutationBundle(
            bundle_id=outcome.bundle_id,
            action_description=parsed.action_description,
            rationale=parsed.rationale,
            draft_narration=parsed.draft_narration,
            mutations=canonicalize_model_mutations(parsed.mutations),
        )


def validate_compilation_fidelity(
    outcome: CandidateOutcome,
    bundle: MutationBundle,
) -> tuple[bool, str | None]:
    """Reject well-typed bundles that do not implement the selected outcome."""

    event_types = {
        mutation.event_type
        for mutation in bundle.mutations
        if isinstance(mutation, AppendEvent)
    }
    mutation_targets = {
        target
        for mutation in bundle.mutations
        for target in (
            getattr(mutation, "target_id", None),
            getattr(mutation, "entity_id", None),
        )
        if isinstance(target, str)
    }
    allowed_targets = {outcome.actor_id, *outcome.target_ids}
    for consequence in outcome.consequences:
        if isinstance(consequence, DialogueConsequence) and not any(
            isinstance(mutation, AppendEvent)
            and mutation.event_type == EventType.DIALOGUE
            and mutation.payload.get("npc_id") == consequence.npc_id
            for mutation in bundle.mutations
        ):
            return False, "COMPILED_DIALOGUE_EVENT_MISSING"
        if isinstance(consequence, CombatConsequence):
            if not any(
                isinstance(mutation, AppendEvent)
                and mutation.event_type == EventType.ATTACK_RESOLVED
                and mutation.payload.get("target_id") == consequence.target_id
                for mutation in bundle.mutations
            ):
                return False, "COMPILED_COMBAT_EVENT_MISSING"
            allowed_targets.update({consequence.attacker_id, consequence.target_id})
            if consequence.severity != "NONE" and not any(
                isinstance(mutation, MutateAttribute)
                and mutation.target_id == consequence.target_id
                and mutation.path == "stats.hp"
                and mutation.op == "ADD"
                and isinstance(mutation.value, (int, float))
                and mutation.value < 0
                for mutation in bundle.mutations
            ):
                return False, "COMPILED_DAMAGE_MISSING"
        if isinstance(consequence, ConditionConsequence) and not any(
            isinstance(mutation, MutateAttribute)
            and mutation.target_id == consequence.target_id
            and mutation.path == consequence.path
            and mutation.value == consequence.condition
            and mutation.op == consequence.op
            for mutation in bundle.mutations
        ):
            return False, "COMPILED_CONDITION_MISSING"
        if isinstance(consequence, TransferConsequence):
            allowed_targets.update(
                {consequence.entity_id, consequence.from_ref, consequence.to_ref}
            )
            if not any(
                isinstance(mutation, TransferEntity)
                and mutation.entity_id == consequence.entity_id
                and mutation.from_ref == consequence.from_ref
                and mutation.to_ref == consequence.to_ref
                for mutation in bundle.mutations
            ):
                return False, "COMPILED_TRANSFER_MISSING"
        if isinstance(consequence, MovementConsequence):
            allowed_targets.update({consequence.entity_id, consequence.target_cell_id})
            if not any(
                isinstance(mutation, MoveEntity)
                and mutation.entity_id == consequence.entity_id
                and mutation.target_cell_id == consequence.target_cell_id
                for mutation in bundle.mutations
            ):
                return False, "COMPILED_MOVEMENT_MISSING"
        if isinstance(consequence, StateConsequence) and not any(
            isinstance(mutation, MutateAttribute)
            and mutation.target_id == consequence.target_id
            and mutation.path == consequence.path
            and mutation.value == consequence.value
            and mutation.op == "SET"
            for mutation in bundle.mutations
        ):
            return False, "COMPILED_STATE_CHANGE_MISSING"
    if not mutation_targets.issubset(allowed_targets):
        return False, "COMPILED_UNRELATED_TARGET"
    if outcome.action_class == ActionClass.SOCIAL and EventType.DIALOGUE not in event_types:
        return False, "COMPILED_DIALOGUE_EVENT_MISSING"
    permitted_events = set()
    if any(isinstance(item, DialogueConsequence) for item in outcome.consequences):
        permitted_events.add(EventType.DIALOGUE)
    if any(isinstance(item, CombatConsequence) for item in outcome.consequences):
        permitted_events.add(EventType.ATTACK_RESOLVED)
    if any(
        isinstance(mutation, AppendEvent) and mutation.event_type not in permitted_events
        for mutation in bundle.mutations
    ):
        return False, "COMPILED_UNRELATED_EVENT"
    return True, None
