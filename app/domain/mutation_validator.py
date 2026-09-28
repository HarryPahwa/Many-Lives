"""Mutation validation and application engine (TDD §8, §13, §14).

Validates candidate MutationBundles against world state snapshots and transforms
valid bundles into database DocumentMutations, DocumentInserts, and Events.
"""

import math
from typing import Any, Mapping, Sequence
from pydantic import ValidationError

from app.domain.mutations import (
    AppendCanonicalEvent,
    AppendEvent,
    ApplyDocumentMutation,
    InsertDocument,
    MoveEntity,
    MutateAttribute,
    MutationBundle,
    TransferEntity,
)
from app.domain.rules import DocumentInsert, DocumentMutation, Resolution, WorldSnapshot
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

NUMERIC_MUTATION_PATHS: frozenset[str] = frozenset(
    path for path in ALLOWED_MUTATION_PATHS if path.startswith("stats.")
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


def _coalesce_document_mutations(
    mutations: Sequence[DocumentMutation],
) -> list[DocumentMutation]:
    """Compile a bundle to one version-checked write per document.

    A candidate may legitimately change several attributes on one entity.  If
    those changes are persisted as separate writes, the first write advances
    the document version and makes every following write fail its own CAS.
    """
    combined: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for mutation in mutations:
        key = (mutation.collection, mutation.document_id)
        if key not in combined:
            order.append(key)
            combined[key] = {
                "expected_version": mutation.expected_version,
                "set_fields": {},
                "inc_fields": {},
                "add_to_set_fields": {},
            }
        entry = combined[key]
        if entry["expected_version"] != mutation.expected_version:
            raise ValueError(
                f"Conflicting expected versions for {mutation.collection} "
                f"document {mutation.document_id}"
            )
        entry["set_fields"].update(mutation.set_fields)
        for path, amount in mutation.inc_fields.items():
            entry["inc_fields"][path] = entry["inc_fields"].get(path, 0) + amount
        entry["add_to_set_fields"].update(mutation.add_to_set_fields)

    return [
        DocumentMutation(
            collection=collection,
            document_id=document_id,
            expected_version=combined[(collection, document_id)]["expected_version"],
            set_fields=combined[(collection, document_id)]["set_fields"],
            inc_fields=combined[(collection, document_id)]["inc_fields"],
            add_to_set_fields=combined[(collection, document_id)]["add_to_set_fields"],
        )
        for collection, document_id in order
    ]


def _get_entity_by_id(world: WorldSnapshot, entity_id: str) -> Mapping[str, Any] | None:
    if _document_id(world.player) == entity_id:
        return world.player
    for c in world.characters:
        if _document_id(c) == entity_id:
            return c
    for item in world.items:
        if _document_id(item) == entity_id:
            return item
    for item in world.container_items:
        if _document_id(item) == entity_id:
            return item
    for item in world.owned_items:
        if _document_id(item) == entity_id:
            return item
    return None


def _numeric_value(value: Any) -> int | float | None:
    """Normalize a model number without accepting booleans or junk strings."""
    if isinstance(value, bool):
        return None
    candidate: int | float
    if isinstance(value, (int, float)):
        candidate = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            candidate = float(text) if any(c in text.lower() for c in (".", "e")) else int(text)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(candidate):
        return None
    return int(candidate) if float(candidate).is_integer() else candidate


def _stats(document: Mapping[str, Any]) -> Mapping[str, Any]:
    value = document.get("character") or document.get("stats") or {}
    return value if isinstance(value, Mapping) else {}


def _document_id(document: Mapping[str, Any]) -> str | None:
    return document.get("entity_id") or document.get("cell_id") or document.get("id")


def _is_accessible_cell(world: WorldSnapshot, target_cell_id: str) -> bool:
    current_cell_id = _document_id(world.current_cell)
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

    if bundle.origin == "MODEL" and bundle.execution is not None:
        return False, "Model bundles cannot supply execution metadata"

    player_id = _document_id(world.player)
    current_cell_id = _document_id(world.current_cell)

    for mutation in bundle.mutations:
        if isinstance(
            mutation, (ApplyDocumentMutation, InsertDocument, AppendCanonicalEvent)
        ):
            if bundle.origin != "DETERMINISTIC":
                return False, f"Mutation kind '{mutation.kind}' is application-only"
            continue

        if isinstance(mutation, MutateAttribute):
            if mutation.path in PROTECTED_PATHS or mutation.path.startswith("id"):
                return False, f"Cannot mutate protected path '{mutation.path}'"

            if mutation.path not in ALLOWED_MUTATION_PATHS:
                return False, f"Mutation path '{mutation.path}' is not permitted"

            if mutation.path in NUMERIC_MUTATION_PATHS:
                if mutation.op not in {"SET", "ADD"}:
                    return False, f"Cannot use op '{mutation.op}' on numeric path '{mutation.path}'"
                if _numeric_value(mutation.value) is None:
                    return False, f"Invalid numeric value for '{mutation.path}': {mutation.value!r}"

            target = _get_entity_by_id(world, mutation.target_id)
            if not target:
                if mutation.target_id == current_cell_id:
                    target = world.current_cell
                else:
                    return False, f"Target entity '{mutation.target_id}' not found in room context"

            if mutation.path == "physical_conditions":
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
            if mutation.event_type == EventType.DIALOGUE:
                npc_id = mutation.payload.get("npc_id")
                npc = next(
                    (c for c in world.characters if _document_id(c) == npc_id), None
                )
                if npc is None:
                    return False, "Dialogue payload must reference a visible character via npc_id"
                if not mutation.payload.get("utterance") and not mutation.payload.get("dialogue_intent"):
                    return False, "Dialogue payload requires an utterance or dialogue_intent"

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
    canonical_inserts: list[DocumentInsert] = []
    events: list[Event] = []
    touched_entity_ids: set[str] = set()
    current_cell_id = _document_id(world.current_cell) or ""
    touched_cell_ids: set[str] = {current_cell_id}

    campaign_id = world.campaign.get("_id") or world.campaign.get("id", "")
    player_id = _document_id(world.player) or ""
    campaign_turn = int(
        world.campaign.get("current_turn", world.campaign.get("turn_count", 0))
    )
    execution = bundle.execution
    expected_turn = execution.expected_turn if execution else campaign_turn
    # Mutations describe the action being committed after the snapshot's
    # current turn.  Event uniqueness and repository CAS both use that next
    # sequence, never the already-committed snapshot sequence.
    turn_sequence = expected_turn + 1
    pending_numeric_values: dict[tuple[str, str], int | float] = {}

    for mutation in bundle.mutations:
        if isinstance(mutation, ApplyDocumentMutation):
            doc_mutations.append(
                DocumentMutation(
                    collection=mutation.collection,
                    document_id=mutation.document_id,
                    expected_version=mutation.expected_version,
                    set_fields=dict(mutation.set_fields),
                    inc_fields=dict(mutation.inc_fields),
                    add_to_set_fields=dict(mutation.add_to_set_fields),
                )
            )

        elif isinstance(mutation, InsertDocument):
            doc_mutations_for_insert = DocumentInsert(
                collection=mutation.collection,
                document=dict(mutation.document),
            )
            # Kept separate because repository commit applies inserts after patches.
            # The list is initialized lazily below for compatibility with model bundles.
            canonical_inserts.append(doc_mutations_for_insert)

        elif isinstance(mutation, AppendCanonicalEvent):
            events.append(mutation.event)

        elif isinstance(mutation, MutateAttribute):
            target_id = mutation.target_id
            touched_entity_ids.add(target_id)
            coll = "cells" if target_id == current_cell_id else "entities"
            target_entity = _get_entity_by_id(world, target_id)
            target_version = target_entity.get("version", 0) if target_entity else 0
            canonical_path = mutation.path
            if mutation.path.startswith("stats.") and target_entity is not None:
                canonical_path = f"character.{mutation.path.removeprefix('stats.')}"

            numeric = _numeric_value(mutation.value)
            if mutation.path == "stats.hp" and target_entity is not None and numeric is not None:
                key = (target_id, mutation.path)
                stats = _stats(target_entity)
                current = pending_numeric_values.get(key, _numeric_value(stats.get("hp")) or 0)
                value = numeric if mutation.op == "SET" else current + numeric
                max_hp = _numeric_value(stats.get("max_hp"))
                value = max(0, value)
                if max_hp is not None:
                    value = min(value, max_hp)
                pending_numeric_values[key] = value
                doc_mutations.append(
                    DocumentMutation(
                        collection=coll,
                        document_id=target_id,
                        expected_version=target_version,
                        set_fields={canonical_path: value},
                    )
                )
            elif mutation.op == "ADD" and numeric is not None:
                doc_mutations.append(
                    DocumentMutation(
                        collection=coll,
                        document_id=target_id,
                        expected_version=target_version,
                        inc_fields={canonical_path: numeric},
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
                        set_fields={
                            canonical_path: numeric
                            if mutation.path in NUMERIC_MUTATION_PATHS
                            else mutation.value
                        },
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
                        "location": {
                            "kind": mutation.location_kind.value,
                            "ref_id": mutation.to_ref,
                            "slot": None,
                        },
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
                        "location": {
                            "kind": LocationKind.CELL.value,
                            "ref_id": mutation.target_cell_id,
                            "slot": None,
                        },
                    },
                )
            )

        elif isinstance(mutation, AppendEvent):
            event_idx = len(events)
            events.append(
                Event(
                    campaign_id=campaign_id,
                    event_id=f"event_{mutation.event_type.value}_{turn_sequence}_{event_idx}",
                    turn_sequence=turn_sequence,
                    event_index=event_idx,
                    turn_id=turn_id or f"turn_{turn_sequence}",
                    type=mutation.event_type,
                    actor_id=player_id,
                    entity_ids=list(touched_entity_ids),
                    cell_id=current_cell_id,
                    payload=dict(mutation.payload),
                    summary=mutation.summary,
                    memory_status=MemoryStatus.NOT_REQUIRED,
                )
            )

    # HP reaching zero has one deterministic meaning.  The model proposes the
    # damage; application code derives death and its canonical event.
    for (target_id, path), final_value in pending_numeric_values.items():
        if path != "stats.hp" or final_value > 0 or target_id == player_id:
            continue
        target = _get_entity_by_id(world, target_id)
        if target is None or _stats(target).get("status") == "DEAD":
            continue
        target_version = int(target.get("version", 0))
        doc_mutations.append(
            DocumentMutation(
                collection="entities",
                document_id=target_id,
                expected_version=target_version,
                set_fields={"character.status": "DEAD"},
            )
        )
        event_idx = len(events)
        events.append(
            Event(
                campaign_id=campaign_id,
                event_id=f"event_{EventType.ENTITY_DIED.value}_{turn_sequence}_{event_idx}",
                turn_sequence=turn_sequence,
                event_index=event_idx,
                turn_id=turn_id or f"turn_{turn_sequence}",
                type=EventType.ENTITY_DIED,
                actor_id=player_id,
                entity_ids=[target_id],
                cell_id=current_cell_id,
                payload={"defender_id": target_id, "killer_ids": [player_id]},
                summary=f"{target_id} died.",
                memory_status=MemoryStatus.PENDING,
            )
        )

    return Resolution(
        accepted=True,
        reason=None,
        events=events,
        mutations=_coalesce_document_mutations(doc_mutations),
        inserts=canonical_inserts,
        touched_entity_ids=(
            list(execution.touched_entity_ids) if execution else list(touched_entity_ids)
        ),
        touched_cell_ids=(
            list(execution.touched_cell_ids) if execution else list(touched_cell_ids)
        ),
        rejected_effects=list(execution.rejected_effects) if execution else [],
        expected_turn=expected_turn,
        expected_campaign_version=(
            execution.expected_campaign_version
            if execution and execution.expected_campaign_version is not None
            else int(world.campaign.get("version", 0))
        ),
        turn_id=(execution.turn_id if execution and execution.turn_id else turn_id),
        current_cell_id=(
            execution.current_cell_id
            if execution and execution.current_cell_id
            else current_cell_id
        ),
        outcome_summary=bundle.draft_narration or bundle.action_description,
    )
