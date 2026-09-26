"""Pure invariant checker for TDD INV-01 through INV-15."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from app.domain.types import FeatureProperty, FeatureStateKey
from app.world.topology import Topology, bfs_distances


@dataclass(frozen=True)
class InvariantFailure:
    invariant_id: str
    message: str
    document_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class InvariantReport:
    checked: int
    failures: tuple[InvariantFailure, ...]

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_document(self) -> dict[str, Any]:
        return {"checked": self.checked,
                "failures": [asdict(failure) for failure in self.failures]}


def static_environment_digest(environment: Mapping[str, Any]) -> str:
    encoded = json.dumps(environment, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()


def check_invariants(campaign: Mapping[str, Any], cells: Sequence[Mapping[str, Any]],
                     entities: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]],
                     turns: Sequence[Mapping[str, Any]], *, expected_key_count: int = 6,
                     queries_scoped: bool = True) -> InvariantReport:
    failures: list[InvariantFailure] = []
    campaign_id = campaign.get("_id")
    by_id = {entity.get("entity_id"): entity for entity in entities}

    def fail(number: int, message: str, *ids: str) -> None:
        failures.append(InvariantFailure(f"INV-{number:02d}", message, tuple(ids)))

    for entity in entities:
        entity_id = str(entity.get("entity_id", ""))
        location = entity.get("location")
        if not isinstance(location, Mapping) or "kind" not in location or "ref_id" not in location:
            fail(1, "entity lacks one authoritative location", entity_id)
            continue
        if location["kind"] in {"INVENTORY", "EQUIPPED"}:
            owner = by_id.get(location["ref_id"])
            if owner is None or owner.get("campaign_id") != campaign_id:
                fail(2, "item owner is missing or cross-campaign", entity_id)

    players = [entity for entity in entities if entity.get("entity_type") == "PLAYER"]
    for player in players:
        player_id = player["entity_id"]
        carried = [entity for entity in entities if entity.get("location", {}).get("kind") == "INVENTORY"
                   and entity["location"].get("ref_id") == player_id]
        equipped = [entity for entity in entities if entity.get("location", {}).get("kind") == "EQUIPPED"
                    and entity["location"].get("ref_id") == player_id]
        slots = [entity["location"].get("slot") for entity in equipped]
        if len(carried) > 6 or slots.count("WEAPON") > 1 or slots.count("ARMOR") > 1:
            fail(3, "player inventory/equipment capacity exceeded", player_id)
    for entity in entities:
        item = entity.get("item")
        if not isinstance(item, Mapping):
            continue
        quantity, maximum = item.get("quantity"), item.get("max_stack")
        if not isinstance(quantity, int) or not isinstance(maximum, int) or not 1 <= quantity <= maximum:
            fail(4, "invalid stack quantity", entity["entity_id"])
        if item.get("subtype") in {"KEY", "TREASURE", "QUEST_ITEM"} and item.get("stackable"):
            fail(4, "unique/quest item is stackable", entity["entity_id"])

    for entity in entities:
        stats = entity.get("character")
        if not isinstance(stats, Mapping):
            continue
        valid = all(isinstance(stats.get(value), int) and not isinstance(stats.get(value), bool)
                    for value in ("hp", "max_hp", "mp", "max_mp"))
        valid = valid and 0 <= stats["hp"] <= stats["max_hp"] and 0 <= stats["mp"] <= stats["max_mp"]
        if entity.get("entity_type") != "PLAYER":
            valid = valid and ((stats.get("status") == "DEAD") == (stats["hp"] == 0))
        if not valid:
            fail(5, "invalid character resource/status state", entity["entity_id"])

    deaths: dict[str, tuple[int, int]] = {}
    for event in sorted(events, key=lambda value: (value.get("turn_sequence", 0), value.get("event_index", 0))):
        position = (event.get("turn_sequence", 0), event.get("event_index", 0))
        if event.get("actor_id") in deaths and position > deaths[event["actor_id"]]:
            fail(6, "dead entity acted after death", str(event["actor_id"]), str(event.get("event_id", "")))
        payload_id = str((event.get("payload") or {}).get("entity_id") or "")
        if event.get("type") == "ENTITY_DIED" and payload_id:
            deaths[payload_id] = position
        elif event.get("type") == "ENTITY_REANIMATED" and payload_id:
            deaths.pop(payload_id, None)

    for entity in entities:
        if entity.get("entity_type") in {"NPC", "ENEMY", "BOSS"}:
            origin = entity.get("origin_cell_id")
            if origin is None:
                fail(7, "non-player character lacks immutable origin", entity["entity_id"])
            elif entity.get("location", {}).get("ref_id") != origin:
                fail(7, "non-player character moved from origin", entity["entity_id"])
    for cell in cells:
        if cell.get("generated"):
            if not cell.get("static_environment_digest"):
                fail(8, "generated cell lacks static-environment baseline", cell["cell_id"])
            else:
                current = cell.get("room", {}).get("static_environment", {})
                if static_environment_digest(current) != cell["static_environment_digest"]:
                    fail(8, "generated static environment changed", cell["cell_id"])

    keys = [entity for entity in entities if entity.get("item", {}).get("subtype") == "KEY"]
    if len(keys) != expected_key_count:
        fail(9, f"expected {expected_key_count} keys, found {len(keys)}")
    door = campaign.get("boss_door", {})
    submitted = set(door.get("submitted_key_ids", ()))
    for key in keys:
        item, location = key["item"], key.get("location", {})
        allowed = location.get("kind") in {"RESERVED", "INVENTORY", "EQUIPPED", "CELL", "CONTAINER"}
        allowed = allowed or (location.get("kind") == "NONE" and key["entity_id"] in submitted
                              and item.get("status") == "CONSUMED")
        if item.get("status") == "DESTROYED" or not allowed:
            fail(9, "key is destroyed or outside allowed locations", key["entity_id"])
    should_unlock = len(submitted) >= int(door.get("required_keys", 0))
    if bool(door.get("unlocked")) != should_unlock or len(submitted) != len(door.get("submitted_key_ids", ())):
        fail(10, "boss door state is inconsistent")

    try:
        topology = Topology(dict(campaign["topology"]))
        if any(cell not in topology.neighbors(neighbor)
               for cell in topology.cells() for neighbor in topology.neighbors(cell)):
            raise ValueError("topology is not symmetric")
        reachable = set(bfs_distances(topology, campaign["spawn_cell_id"]))
        required = {campaign["boss_cell_id"]}
        required.update(key["location"]["ref_id"] for key in keys
                        if key.get("location", {}).get("kind") == "RESERVED")
        if not required.issubset(reachable):
            fail(11, "boss or reserved key is unreachable")
    except (KeyError, ValueError, TypeError) as exc:
        fail(11, f"invalid topology: {exc}")

    event_ids = {event.get("event_id") for event in events}
    if any(int(event.get("turn_sequence", 0)) > int(campaign.get("current_turn", 0)) for event in events):
        fail(12, "event sequence exceeds campaign current turn")
    for turn in turns:
        if turn.get("status") in {"COMMITTED", "NARRATED", "NARRATION_FAILED"}:
            if turn.get("accepted_effect_types") and (not turn.get("event_ids")
                    or not set(turn["event_ids"]).issubset(event_ids)):
                fail(12, "committed effectful turn lacks events", str(turn.get("turn_id", "")))

    documents = [*cells, *entities, *events, *turns]
    if not queries_scoped or any(document.get("campaign_id") != campaign_id for document in documents):
        fail(13, "query or document is not campaign scoped")
    for player in players:
        if int(player.get("character", {}).get("dodge_pct", 0)) > 40:
            fail(14, "player dodge exceeds cap", player["entity_id"])
    properties = {member.value for member in FeatureProperty}
    state_keys = {member.value for member in FeatureStateKey}
    for cell in cells:
        for feature in cell.get("features", ()):
            if not set(feature.get("properties", ())).issubset(properties):
                fail(15, "unknown feature property", cell["cell_id"], feature.get("feature_id", ""))
            if not set(feature.get("state", {})).issubset(state_keys):
                fail(15, "unknown feature state key", cell["cell_id"], feature.get("feature_id", ""))
    return InvariantReport(15, tuple(failures))
