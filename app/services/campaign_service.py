"""Campaign creation service (TDD §7.2, A2)."""

from __future__ import annotations

import os
from base64 import b32encode
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import secrets
from typing import Any

import yaml

from app.persistence.repositories import Repository
from app.services.room_service import DressRoom, generate_room
from app.world.placement import place_world
from app.world.topology import generate_topology, parse_cell_key


CAMPAIGN_ID_PATTERN = re.compile(r"^cmp_[a-z0-9]{12}$")
DEFAULT_BALANCE_FILE = Path(__file__).parents[2] / "config" / "balance.yaml"


@dataclass(frozen=True)
class CampaignCreationResult:
    campaign_id: str
    seed: int
    player_id: str
    spawn_cell_id: str
    boss_cell_id: str


def _new_campaign_id() -> str:
    token = b32encode(secrets.token_bytes(8)).decode("ascii").lower().rstrip("=")
    return f"cmp_{token[:12]}"


def _load_balance() -> dict[str, Any]:
    path = Path(os.getenv("BALANCE_FILE", str(DEFAULT_BALANCE_FILE)))
    with path.open(encoding="utf-8") as stream:
        balance = yaml.safe_load(stream)
    if not isinstance(balance, dict):
        raise ValueError("Balance configuration must be a mapping")
    return balance


def create_campaign(
    repository: Repository,
    player_name: str,
    *,
    seed: int | None = None,
    campaign_id: str | None = None,
    dress_room: DressRoom | None = None,
) -> CampaignCreationResult:
    """Build and atomically persist one initial campaign."""
    normalized_name = player_name.strip()
    if not normalized_name or len(normalized_name) > 40:
        raise ValueError("player_name must contain 1 to 40 non-whitespace characters")

    campaign_id = campaign_id or _new_campaign_id()
    if CAMPAIGN_ID_PATTERN.fullmatch(campaign_id) is None:
        raise ValueError("campaign_id must be 'cmp_' followed by 12 lowercase letters/digits")
    if seed is None:
        seed = secrets.randbits(63)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**63:
        raise ValueError("seed must be a 63-bit non-negative integer")

    balance = _load_balance()
    world = balance["world"]
    width = world["width"]
    height = world["height"]
    topology = generate_topology(
        seed,
        width=width,
        height=height,
        extra_edge_probability=world["extra_edge_probability"],
    )
    placement = place_world(
        topology,
        seed,
        width=width,
        height=height,
        min_spawn_boss_distance=world["min_spawn_boss_distance"],
        key_reservations=world["key_reservations"],
        max_spawn_attempts=world["boss_placement_attempts"],
    )

    now = datetime.now(UTC)
    player_id = "player_1"
    key_ids = [f"item_k{index}" for index in range(1, world["key_reservations"] + 1)]
    key_by_cell = dict(zip(placement.key_reservation_cells, key_ids, strict=True))
    persisted_config = {
        "grid": {"width": width, "height": height},
        "keys_required": world["keys_required"],
        "key_reservations": world["key_reservations"],
        "min_spawn_boss_distance": world["min_spawn_boss_distance"],
        "extra_edge_probability": world["extra_edge_probability"],
    }
    campaign = {
        "_id": campaign_id,
        "schema_version": 1,
        "seed": seed,
        "status": "ACTIVE",
        "config": persisted_config,
        "topology": topology.to_dict(),
        "distance_to_boss": placement.distance_to_boss,
        "spawn_cell_id": placement.spawn_cell_id,
        "boss_cell_id": placement.boss_cell_id,
        "key_item_ids": key_ids,
        "boss_door": {
            "required_keys": world["keys_required"],
            "submitted_key_ids": [],
            "unlocked": False,
        },
        "current_turn": 0,
        "player_ids": [player_id],
        "turn_order": [player_id],
        "turn_index": 0,
        "round": 1,
        "active_context_policy_version": 1,
        "winner_player_id": None,
        "version": 0,
        "created_at": now,
        "updated_at": now,
    }

    cells = []
    for cell_id in topology.cells():
        x, y = parse_cell_key(cell_id)
        reserved_key = key_by_cell.get(cell_id)
        cells.append(
            {
                "_id": f"{campaign_id}:{cell_id}",
                "campaign_id": campaign_id,
                "schema_version": 1,
                "cell_id": cell_id,
                "x": x,
                "y": y,
                "generated": False,
                "generation_status": "UNGENERATED",
                "generation_started_at": None,
                "generation_policy_version": 1,
                "generation_source": None,
                "danger_tier": placement.danger_tiers[cell_id],
                "reservations": {
                    "boss": cell_id == placement.boss_cell_id,
                    "key_item_ids": [reserved_key] if reserved_key else [],
                    "quest_obligation_ids": [],
                },
                "room": None,
                "features": [],
                "visited_by": [player_id] if cell_id == placement.spawn_cell_id else [],
                "version": 0,
                "last_updated_turn": 0,
            }
        )

    player_defaults = balance["player"]
    player = {
        "_id": f"{campaign_id}:{player_id}",
        "campaign_id": campaign_id,
        "schema_version": 1,
        "entity_id": player_id,
        "entity_type": "PLAYER",
        "name": normalized_name,
        "description": "The active player character.",
        "location": {"kind": "CELL", "ref_id": placement.spawn_cell_id, "slot": None},
        "character": {
            "level": 1,
            "xp": 0,
            "pending_level_ups": 0,
            "hp": player_defaults["max_hp"],
            "max_hp": player_defaults["max_hp"],
            "mp": player_defaults["max_mp"],
            "max_mp": player_defaults["max_mp"],
            "attack": player_defaults["attack"],
            "defense": player_defaults["defense"],
            "speed": player_defaults["speed"],
            "dodge_pct": player_defaults["dodge_pct"],
            "skill": player_defaults["skill"],
            "status": "ALIVE",
            "faction": "PLAYER",
            "persona": None,
            "traits": [],
            "alerted": False,
            "assisting": False,
            "disposition": {},
            "knowledge": [],
        },
        "player": {
            "spawn_cell_id": placement.spawn_cell_id,
            "discovered_cell_ids": [placement.spawn_cell_id],
            "rumored_cell_ids": [],
            "new_cells_since_death": 0,
            "damage_dealt_since_death": 0,
            "deaths": 0,
            "kills": 0,
        },
        "version": 0,
        "created_turn": 0,
        "updated_turn": 0,
    }

    keys = [
        _key_document(
            campaign_id,
            key_id,
            cell_id,
            placement.danger_tiers[cell_id],
        )
        for cell_id, key_id in key_by_cell.items()
    ]
    events = [
        _initial_event(
            campaign_id,
            "evt_0_0",
            0,
            "CAMPAIGN_CREATED",
            player_id,
            [player_id],
            placement.spawn_cell_id,
            {"seed": seed},
            "Campaign created.",
            now,
        ),
        _initial_event(
            campaign_id,
            "evt_0_1",
            1,
            "PLAYER_SPAWNED",
            player_id,
            [player_id],
            placement.spawn_cell_id,
            {"player_name": normalized_name},
            f"{normalized_name} spawned at {placement.spawn_cell_id}.",
            now,
        ),
    ]
    repository.create_campaign(campaign, cells, [player, *keys], events)
    generate_room(
        repository,
        campaign_id,
        placement.spawn_cell_id,
        dress_room=dress_room,
    )
    return CampaignCreationResult(
        campaign_id=campaign_id,
        seed=seed,
        player_id=player_id,
        spawn_cell_id=placement.spawn_cell_id,
        boss_cell_id=placement.boss_cell_id,
    )


def _key_document(
    campaign_id: str, key_id: str, cell_id: str, tier: int
) -> dict[str, Any]:
    return {
        "_id": f"{campaign_id}:{key_id}",
        "campaign_id": campaign_id,
        "schema_version": 1,
        "entity_id": key_id,
        "entity_type": "ITEM",
        "name": "sealed brass key",
        "description": "One of the keys required to open the boss door.",
        "location": {"kind": "RESERVED", "ref_id": cell_id, "slot": None},
        "item": {
            "subtype": "KEY",
            "tier": tier,
            "stackable": False,
            "quantity": 1,
            "max_stack": 1,
            "quest_critical": True,
            "properties": [],
            "attack_bonus": 0,
            "armor_bonus": 0,
            "effects": [],
            "spell": None,
            "hidden": False,
            "concealment_dc": None,
            "guarded_by": [],
            "status": "ACTIVE",
        },
        "version": 0,
        "created_turn": 0,
        "updated_turn": 0,
    }


def _initial_event(
    campaign_id: str,
    event_id: str,
    event_index: int,
    event_type: str,
    actor_id: str,
    entity_ids: list[str],
    cell_id: str,
    payload: dict[str, Any],
    summary: str,
    created_at: datetime,
) -> dict[str, Any]:
    return {
        "_id": f"{campaign_id}:{event_id}",
        "campaign_id": campaign_id,
        "schema_version": 1,
        "event_id": event_id,
        "turn_sequence": 0,
        "event_index": event_index,
        "turn_id": "campaign_creation",
        "type": event_type,
        "actor_id": actor_id,
        "entity_ids": entity_ids,
        "cell_id": cell_id,
        "payload": payload,
        "summary": summary,
        "memory_status": "NOT_REQUIRED",
        "memory_attempts": 0,
        "created_at": created_at,
    }
