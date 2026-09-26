"""Atomic room generation service (TDD §7.3)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import os
from typing import Any

import yaml

from app.domain.types import RoomDressing, RoomPlan
from app.persistence.repositories import Repository
from app.world.fallback import build_fallback_dressing
from app.world.room_planner import PlannedRoom, plan_room
from app.world.room_validation import validate_room_dressing


DressRoom = Callable[[RoomPlan, tuple[str, ...]], RoomDressing]
DEFAULT_BALANCE_FILE = Path(__file__).parents[2] / "config" / "balance.yaml"


class RoomGenerationInProgress(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedRoom:
    campaign_id: str
    cell_id: str
    generation_source: str
    entity_ids: tuple[str, ...]
    cell: dict[str, Any]


def _load_balance() -> dict[str, Any]:
    path = Path(os.getenv("BALANCE_FILE", str(DEFAULT_BALANCE_FILE)))
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def generate_room(
    repository: Repository,
    campaign_id: str,
    cell_id: str,
    dress_room: DressRoom | None = None,
) -> GeneratedRoom:
    """Claim, plan, dress, validate, and atomically persist one room."""
    balance = _load_balance()
    claim = repository.claim_room_generation(
        campaign_id,
        cell_id,
        stale_after_seconds=balance["generation"]["generation_claim_timeout_seconds"],
    )
    if claim["generation_status"] == "GENERATED":
        return _generated_result(repository, campaign_id, claim)
    if claim["generation_status"] != "GENERATING":
        raise RoomGenerationInProgress(f"Room generation already active: {cell_id}")

    campaign = repository.get_campaign(campaign_id)
    if campaign is None:
        raise ValueError(f"Campaign not found: {campaign_id}")
    campaign_for_planning = dict(campaign)
    campaign_for_planning["ungenerated_key_cell_ids"] = repository.unplaced_key_cells(
        campaign_id
    )
    planned = plan_room(
        seed=campaign["seed"],
        cell=claim,
        campaign=campaign_for_planning,
        balance=balance,
    )

    errors: list[str] = []
    dressing: RoomDressing | None = None
    source = "MODEL"
    if dress_room is not None:
        for _attempt in range(balance["generation"]["dresser_retries"] + 1):
            try:
                candidate = dress_room(planned.dressing_plan, tuple(errors))
                dressing = validate_room_dressing(planned, candidate)
                break
            except Exception as exc:
                # Provider and validation failures share the same bounded retry
                # path. SystemExit/KeyboardInterrupt remain uncaught.
                errors.append(str(exc))
    if dressing is None:
        source = "FALLBACK"
        dressing = build_fallback_dressing(planned, seed=campaign["seed"])

    features, new_entities, reserved_updates = _materialize(
        campaign_id,
        cell_id,
        planned,
        dressing,
    )
    entity_ids = repository.commit_generated_room(
        campaign_id=campaign_id,
        cell_id=cell_id,
        expected_version=claim.get("version", 0),
        room={
            "archetype": planned.dressing_plan.archetype.value,
            "name": dressing.room_name,
            "static_environment": dressing.static_environment.model_dump(mode="json"),
        },
        features=features,
        new_entities=new_entities,
        reserved_updates=reserved_updates,
        generation_source=source,
    )
    cell = repository.get_cell(campaign_id, cell_id)
    assert cell is not None
    return GeneratedRoom(campaign_id, cell_id, source, tuple(entity_ids), cell)


def _generated_result(
    repository: Repository, campaign_id: str, cell: dict[str, Any]
) -> GeneratedRoom:
    entity_ids = tuple(
        document["entity_id"]
        for document in repository.entities_in_cell(campaign_id, cell["cell_id"])
    )
    return GeneratedRoom(
        campaign_id,
        cell["cell_id"],
        cell.get("generation_source") or "UNKNOWN",
        entity_ids,
        cell,
    )


def _materialize(
    campaign_id: str,
    cell_id: str,
    planned: PlannedRoom,
    dressing: RoomDressing,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    feature_ids: dict[str, str] = {}
    features = []
    for index, feature in enumerate(dressing.features, start=1):
        feature_id = f"feat_{cell_id}_{index}"
        if feature.slot_id is not None:
            feature_ids[feature.slot_id] = feature_id
        features.append(
            {
                "feature_id": feature_id,
                "kind": feature.kind,
                "name": feature.name,
                "properties": [property_.value for property_ in feature.properties],
                "state": dict(feature.initial_state),
                "created_by": "GENERATION",
            }
        )

    entity_ids: dict[str, str] = {}
    new_entities: list[dict[str, Any]] = []
    dressed_entities = {entity.slot_id: entity for entity in dressing.entities}
    for index, slot in enumerate(planned.dressing_plan.entity_slots, start=1):
        dressed = dressed_entities[slot.slot_id]
        prefix = slot.role.value.lower()
        entity_id = f"{prefix}_{cell_id.removeprefix('cell_')}_{index}"
        entity_ids[slot.slot_id] = entity_id
        mechanics = planned.entity_mechanics[slot.slot_id]
        stats = mechanics.stats
        knowledge = [
            {**fact.model_dump(mode="json"), "revealed_to": []} for fact in mechanics.facts
        ]
        new_entities.append(
            {
                "_id": f"{campaign_id}:{entity_id}",
                "campaign_id": campaign_id,
                "schema_version": 1,
                "entity_id": entity_id,
                "entity_type": slot.role.value,
                "name": dressed.name,
                "description": dressed.description,
                "location": {"kind": "CELL", "ref_id": cell_id, "slot": None},
                "character": {
                    "level": stats["level"],
                    "xp": 0,
                    "pending_level_ups": 0,
                    "hp": stats["hp"],
                    "max_hp": stats["hp"],
                    "mp": 0,
                    "max_mp": 0,
                    "attack": stats["attack"],
                    "defense": stats["defense"],
                    "speed": stats["speed"],
                    "dodge_pct": stats["dodge_pct"],
                    "skill": stats["skill"],
                    "status": "ALIVE",
                    "faction": mechanics.faction,
                    "persona": dressed.persona,
                    "traits": dressed.traits,
                    "alerted": False,
                    "assisting": False,
                    "disposition": {},
                    "knowledge": knowledge,
                },
                "version": 0,
                "created_turn": 0,
                "updated_turn": 0,
            }
        )

    reserved_updates: list[dict[str, Any]] = []
    dressed_items = {item.slot_id: item for item in dressing.items}
    for index, slot in enumerate(planned.dressing_plan.item_slots, start=1):
        mechanics = planned.item_mechanics[slot.slot_id]
        dressed = dressed_items[slot.slot_id]
        reserved_id = planned.reserved_item_by_slot.get(slot.slot_id)
        item_id = reserved_id or f"item_{cell_id.removeprefix('cell_')}_{index}"
        location = _item_location(slot, cell_id, feature_ids, entity_ids)
        item_extension = {
            "subtype": mechanics.subtype.value,
            "tier": mechanics.tier,
            "stackable": mechanics.stackable,
            "quantity": mechanics.quantity,
            "max_stack": mechanics.max_stack,
            "quest_critical": mechanics.quest_critical,
            "properties": ["consumable"] if mechanics.effects else [],
            "attack_bonus": mechanics.attack_bonus,
            "armor_bonus": mechanics.armor_bonus,
            "effects": [dict(effect) for effect in mechanics.effects],
            "spell": None,
            "hidden": mechanics.hidden,
            "concealment_dc": mechanics.concealment_dc,
            "guarded_by": [entity_ids[slot.guard_slot_id]] if slot.guard_slot_id else [],
            "status": "ACTIVE",
        }
        common = {
            "name": dressed.name,
            "description": dressed.description,
            "location": location,
            "item": item_extension,
            "updated_turn": 0,
        }
        if reserved_id:
            reserved_updates.append({"entity_id": reserved_id, "set": common})
        else:
            new_entities.append(
                {
                    "_id": f"{campaign_id}:{item_id}",
                    "campaign_id": campaign_id,
                    "schema_version": 1,
                    "entity_id": item_id,
                    "entity_type": "ITEM",
                    **common,
                    "version": 0,
                    "created_turn": 0,
                }
            )
    return features, new_entities, reserved_updates


def _item_location(slot, cell_id, feature_ids, entity_ids) -> dict[str, Any]:
    if slot.placement.value == "CONTAINER":
        return {"kind": "CONTAINER", "ref_id": feature_ids[slot.container_slot_id], "slot": None}
    if slot.placement.value == "HELD":
        return {"kind": "INVENTORY", "ref_id": entity_ids[slot.holder_slot_id], "slot": None}
    return {"kind": "CELL", "ref_id": cell_id, "slot": None}
