"""Strict validation of model-proposed room dressing (TDD §14.7)."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.types import RoomDressing
from app.world.room_planner import PlannedRoom


STATE_VALUES = {
    "open_state": {"open", "closed"},
    "lock_state": {"locked", "unlocked"},
    "condition": {"intact", "broken"},
    "orientation": {"upright", "overturned"},
    "light_state": {"lit", "unlit"},
}
MODERN_WORDS = {"phone", "computer", "internet", "laser", "automobile"}


@dataclass(frozen=True)
class RoomValidationError(ValueError):
    errors: tuple[str, ...]

    def __str__(self) -> str:
        return "; ".join(self.errors)


def validate_room_dressing(planned: PlannedRoom, dressing: RoomDressing) -> RoomDressing:
    errors: list[str] = []
    plan = planned.dressing_plan

    _validate_slots(
        "entity",
        {slot.slot_id for slot in plan.entity_slots},
        [entity.slot_id for entity in dressing.entities],
        errors,
    )
    _validate_slots(
        "item",
        {slot.slot_id for slot in plan.item_slots},
        [item.slot_id for item in dressing.items],
        errors,
    )
    minimum, maximum = plan.feature_range
    if not minimum <= len(dressing.features) <= maximum:
        errors.append(f"feature count must be between {minimum} and {maximum}")
    if len(dressing.features) > 12:
        errors.append("feature count exceeds canonical maximum 12")

    features_by_slot = {
        feature.slot_id: feature for feature in dressing.features if feature.slot_id is not None
    }
    for slot in plan.item_slots:
        if slot.placement.value in {"CONTAINER", "HIDDEN"}:
            feature = features_by_slot.get(slot.container_slot_id)
            required = "container" if slot.placement.value == "CONTAINER" else "concealing"
            if feature is None or required not in {property_.value for property_ in feature.properties}:
                errors.append(f"{slot.slot_id} requires {required} feature {slot.container_slot_id}")

    for feature in dressing.features:
        properties = {property_.value for property_ in feature.properties}
        for key, value in feature.initial_state.items():
            if key not in STATE_VALUES or value not in STATE_VALUES[key]:
                errors.append(f"invalid state {key}={value} on {feature.name}")
            if key == "orientation" and value == "overturned" and "movable" not in properties:
                errors.append(f"overturned feature {feature.name} must be movable")
            if key == "condition" and value == "broken" and "breakable" not in properties:
                errors.append(f"broken feature {feature.name} must be breakable")
            if key == "light_state" and value == "lit" and not properties.intersection(
                {"light_source", "flammable"}
            ):
                errors.append(f"lit feature {feature.name} needs a light property")
            if key in {"open_state", "lock_state"} and "container" not in properties and feature.kind.lower() != "door":
                errors.append(f"{key} on {feature.name} requires container or door")

    names = [
        dressing.room_name,
        *(feature.name for feature in dressing.features),
        *(entity.name for entity in dressing.entities),
        *(item.name for item in dressing.items),
    ]
    normalized = [name.strip().casefold() for name in names]
    if any(not name for name in normalized):
        errors.append("room-local names cannot be blank")
    if len(normalized) != len(set(normalized)):
        errors.append("room-local names must be unique")

    text_fields = [
        dressing.room_name,
        dressing.static_environment.lighting,
        dressing.static_environment.smell,
        dressing.static_environment.architectural_notes,
        *dressing.static_environment.materials,
        *(feature.name for feature in dressing.features),
        *(entity.name for entity in dressing.entities),
        *(entity.description for entity in dressing.entities),
        *(entity.persona or "" for entity in dressing.entities),
        *(item.name for item in dressing.items),
        *(item.description for item in dressing.items),
    ]
    for text in text_fields:
        words = {word.strip(".,;:!?()[]{}\"").casefold() for word in text.split()}
        forbidden = words.intersection(MODERN_WORDS)
        if forbidden:
            errors.append(f"modern technology term rejected: {sorted(forbidden)[0]}")

    role_by_slot = {slot.slot_id: slot.role.value for slot in plan.entity_slots}
    for entity in dressing.entities:
        if role_by_slot.get(entity.slot_id) == "NPC" and not (entity.persona or "").strip():
            errors.append(f"NPC {entity.slot_id} requires persona")
        if role_by_slot.get(entity.slot_id) != "NPC" and entity.persona is not None:
            errors.append(f"non-NPC {entity.slot_id} cannot have persona")

    if errors:
        raise RoomValidationError(tuple(errors))
    return dressing


def _validate_slots(
    kind: str, planned: set[str], supplied: list[str], errors: list[str]
) -> None:
    if len(supplied) != len(set(supplied)):
        errors.append(f"duplicate {kind} slots")
    missing = sorted(planned.difference(supplied))
    extra = sorted(set(supplied).difference(planned))
    if missing:
        errors.append(f"missing {kind} slots: {', '.join(missing)}")
    if extra:
        errors.append(f"unplanned {kind} slots: {', '.join(extra)}")
