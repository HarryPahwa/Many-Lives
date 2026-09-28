"""Mutation validation and application engine (TDD §8, §13, §14).

Validates candidate MutationBundles against world state snapshots and transforms
valid bundles into database DocumentMutations, DocumentInserts, and Events.
"""

from typing import Any, Mapping, Sequence
from pydantic import ValidationError

from app.domain.mutations import (
    AppendEvent,
    MoveEntity,
    MutateAttribute,
    MutationBundle,
    TransferEntity,
)
from app.domain.rules import DocumentMutation, Resolution, WorldSnapshot
from app.domain.types import (
    Event,
    EventType,
    LocationKind,
    MemoryStatus,
    MentalCondition,
    PhysicalCondition,
)


ALLOWED_MUTATION_PATHS: frozenset[str] = frozenset(
    {
        "stats.hp",
        "stats.mp",
        "stats.attack",
        "stats.defense",
        "stats.speed",
        "stats.dodge_pct",
        "stats.skill",
        "stats.xp",
        "stats.level",
        "status",
        "disposition",
        "light_state",
        "discovered",
        "visited",
        "physical_conditions",
        "mental_conditions",
    }
)

PROTECTED_PATHS: frozenset[str] = frozenset(
    {
        "id",
        "_id",
        "campaign_id",
        "type",
        "created_at",
        "version",
    }
)


def _get_entity_by_id(world: WorldSnapshot, entity_id: str) -> Mapping[str, Any] | None:
    if world.player.get("id") == entity_id:
        return world.player
    for c in world.characters:
        if c.get("id") == entity_id:
            return c
    for item in world.items:
        if item.get("id") == entity_id:
            return item
    for item in world.container_items:
        if item.get("id") == entity_id:
            return item
    for item in world.owned_items:
        if item.get("id") == entity_id:
            return item
    return None


def _is_accessible_cell(world: WorldSnapshot, target_cell_id: str) -> bool:
    current_cell_id = world.current_cell.get("id")
    if target_cell_id == current_cell_id:
        return True
    if world.destination_cell and world.destination_cell.get("id") == target_cell_id:
        return True
    return False


def validate_candidate_bundle(
    world: WorldSnapshot, bundle: MutationBundle
) -> tuple[bool, str | None]:
    """Validates a candidate mutation bundle against the current world snapshot.

    Returns (True, None) if valid, or (False, reason) if invalid.
    """
    if not isinstance(bundle, MutationBundle):
        return False, "Bundle is not a valid MutationBundle instance"

    player_id = world.player.get("id")
    current_cell_id = world.current_cell.get("id")

    for mutation in bundle.mutations:
        if isinstance(mutation, MutateAttribute):
            if mutation.path in PROTECTED_PATHS or mutation.path.startswith("id"):
                return False, f"Cannot mutate protected path '{mutation.path}'"

            if mutation.path not in ALLOWED_MUTATION_PATHS:
                return False, f"Mutation path '{mutation.path}' is not permitted"

            target = _get_entity_by_id(world, mutation.target_id)
            if not target:
                if mutation.target_id == current_cell_id:
                    target = world.current_cell
                else:
                    return False, f"Target entity '{mutation.target_id}' not found in room context"

            if mutation.path == "stats.hp":
                if mutation.op == "SET":
                    if not isinstance(mutation.value, (int, float)) or mutation.value < 0:
                        return False, f"Invalid HP value: {mutation.value}"

            elif mutation.path == "physical_conditions":
                if mutation.op not in {"ADD", "REMOVE"}:
                    return False, f"Cannot use op '{mutation.op}' on physical_conditions; only ADD or REMOVE are permitted"
                if mutation.target_id == current_cell_id or not (target.get("stats") is not None or target.get("character") is not None or mutation.target_id == player_id or target in world.characters):
                    return False, f"Target '{mutation.target_id}' is not a character entity"
                try:
                    PhysicalCondition(mutation.value)
                except ValueError:
                    return False, f"Invalid PhysicalCondition '{mutation.value}'"

            elif mutation.path == "mental_conditions":
                if mutation.op not in {"ADD", "REMOVE"}:
                    return False, f"Cannot use op '{mutation.op}' on mental_conditions; only ADD or REMOVE are permitted"
                if mutation.target_id == current_cell_id or not (target.get("stats") is not None or target.get("character") is not None or mutation.target_id == player_id or target in world.characters):
                    return False, f"Target '{mutation.target_id}' is not a character entity"
                try:
                    MentalCondition(mutation.value)
                except ValueError:
                    return False, f"Invalid MentalCondition '{mutation.value}'"

        elif isinstance(mutation, TransferEntity):
            item = _get_entity_by_id(world, mutation.entity_id)
            if not item:
                return False, f"Transfer entity '{mutation.entity_id}' not found"

            # Check valid location kinds
            if mutation.location_kind not in LocationKind:
                return False, f"Invalid LocationKind '{mutation.location_kind}'"

        elif isinstance(mutation, MoveEntity):
            if mutation.entity_id != player_id:
                # Only player movement or active NPC movement allowed
                entity = _get_entity_by_id(world, mutation.entity_id)
                if not entity:
                    return False, f"Move target entity '{mutation.entity_id}' not found"

            if not _is_accessible_cell(world, mutation.target_cell_id):
                return (
                    False,
                    f"Target cell '{mutation.target_cell_id}' is not accessible from '{current_cell_id}'",
                )

        elif isinstance(mutation, AppendEvent):
            if not isinstance(mutation.event_type, EventType):
                return False, f"Invalid EventType '{mutation.event_type}'"

    return True, None


def apply_mutation_bundle(
    world: WorldSnapshot,
    bundle: MutationBundle,
    turn_id: str | None = None,
) -> Resolution:
    """Applies a validated MutationBundle and generates a domain Resolution."""
    is_valid, reason = validate_candidate_bundle(world, bundle)
    if not is_valid:
        return Resolution(
            accepted=False,
            reason=reason,
            turn_id=turn_id,
            current_cell_id=world.current_cell.get("id"),
            outcome_summary=reason or "Validation failed",
        )

    doc_mutations: list[DocumentMutation] = []
    events: list[Event] = []
    touched_entity_ids: set[str] = set()
    touched_cell_ids: set[str] = {world.current_cell.get("id", "")}

    campaign_id = world.campaign.get("id", "")
    player_id = world.player.get("id", "")
    current_turn = world.campaign.get("turn_count", 0)

    for mutation in bundle.mutations:
        if isinstance(mutation, MutateAttribute):
            target_id = mutation.target_id
            touched_entity_ids.add(target_id)
            coll = "cells" if target_id == world.current_cell.get("id") else "entities"
            target_entity = _get_entity_by_id(world, target_id)
            target_version = target_entity.get("version", 0) if target_entity else 0

            if mutation.op == "ADD" and isinstance(mutation.value, (int, float)):
                doc_mutations.append(
                    DocumentMutation(
                        collection=coll,
                        document_id=target_id,
                        expected_version=target_version,
                        inc_fields={mutation.path: int(mutation.value)},
                    )
                )
            elif mutation.path in {"physical_conditions", "mental_conditions"}:
                current_conditions = list(
                    (target_entity.get("character", {}) if target_entity else {}).get(mutation.path)
                    or (target_entity.get(mutation.path) if target_entity else [])
                    or []
                )
                if mutation.op == "ADD":
                    if mutation.value not in current_conditions:
                        current_conditions.append(str(mutation.value))
                elif mutation.op == "REMOVE":
                    current_conditions = [c for c in current_conditions if c != mutation.value]
                current_conditions.sort()
                doc_mutations.append(
                    DocumentMutation(
                        collection=coll,
                        document_id=target_id,
                        expected_version=target_version,
                        set_fields={
                            f"character.{mutation.path}" if target_entity and "character" in target_entity else mutation.path: current_conditions
                        },
                    )
                )
            else:
                doc_mutations.append(
                    DocumentMutation(
                        collection=coll,
                        document_id=target_id,
                        expected_version=target_version,
                        set_fields={mutation.path: mutation.value},
                    )
                )

        elif isinstance(mutation, TransferEntity):
            entity_id = mutation.entity_id
            touched_entity_ids.add(entity_id)
            item = _get_entity_by_id(world, entity_id)
            item_version = item.get("version", 0) if item else 0

            doc_mutations.append(
                DocumentMutation(
                    collection="entities",
                    document_id=entity_id,
                    expected_version=item_version,
                    set_fields={
                        "location_kind": mutation.location_kind.value,
                        "location_id": mutation.to_ref,
                    },
                )
            )

        elif isinstance(mutation, MoveEntity):
            entity_id = mutation.entity_id
            touched_entity_ids.add(entity_id)
            touched_cell_ids.add(mutation.target_cell_id)
            entity = _get_entity_by_id(world, entity_id)
            version = entity.get("version", 0) if entity else 0

            doc_mutations.append(
                DocumentMutation(
                    collection="entities",
                    document_id=entity_id,
                    expected_version=version,
                    set_fields={
                        "location_kind": LocationKind.CELL.value,
                        "location_id": mutation.target_cell_id,
                    },
                )
            )

        elif isinstance(mutation, AppendEvent):
            event_idx = len(events)
            events.append(
                Event(
                    campaign_id=campaign_id,
                    event_id=f"event_{mutation.event_type.value}_{current_turn}_{event_idx}",
                    turn_sequence=current_turn,
                    event_index=event_idx,
                    turn_id=turn_id or f"turn_{current_turn}",
                    type=mutation.event_type,
                    actor_id=player_id,
                    entity_ids=list(touched_entity_ids),
                    cell_id=world.current_cell.get("id", ""),
                    payload=mutation.payload,
                    summary=mutation.summary,
                    memory_status=MemoryStatus.NOT_REQUIRED,
                )
            )

    return Resolution(
        accepted=True,
        reason=None,
        events=events,
        mutations=doc_mutations,
        touched_entity_ids=list(touched_entity_ids),
        touched_cell_ids=list(touched_cell_ids),
        turn_id=turn_id,
        current_cell_id=world.current_cell.get("id"),
        outcome_summary=bundle.draft_narration or bundle.action_description,
    )
