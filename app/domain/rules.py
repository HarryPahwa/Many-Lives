"""Deterministic rules engine (TDD §13 & §14).

Validates preconditions, resolves mechanics, and produces state updates and
immutable event drafts. Pure domain logic: takes domain types (not Mongo
documents) as input.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol, Sequence

from app.domain.combat import hostile_order, resolve_attack
from app.domain.checks import adjust_disposition, resolve_check
from app.domain.death import award_xp, kill_xp, resolve_player_death
from app.domain.door import can_enter_boss, claim_treasure, submit_keys
from app.domain.inventory import (
    drop_item,
    equip_item,
    take_item,
    unequip_item,
    use_item,
)

from app.domain.types import (
    ActionIntent,
    ActionType,
    Event,
    EventType,
    CheckKind,
    DispositionState,
    MemoryStatus,
)
from app.domain.rng import TurnRng
from app.world.topology import Topology


@dataclass(frozen=True)
class DocumentMutation:
    collection: Literal["campaigns", "cells", "entities"]
    document_id: str
    expected_version: int
    set_fields: Mapping[str, Any] = field(default_factory=dict)
    inc_fields: Mapping[str, int] = field(default_factory=dict)
    add_to_set_fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentInsert:
    collection: Literal["entities"]
    document: Mapping[str, Any]


@dataclass
class Resolution:
    accepted: bool
    reason: str | None = None
    effects: list[dict[str, Any]] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    state_updates: dict[str, Any] = field(default_factory=dict)
    mutations: list[DocumentMutation] = field(default_factory=list)
    inserts: list[DocumentInsert] = field(default_factory=list)
    touched_entity_ids: list[str] = field(default_factory=list)
    touched_cell_ids: list[str] = field(default_factory=list)
    rejected_effects: list[dict[str, Any]] = field(default_factory=list)
    expected_turn: int | None = None
    expected_campaign_version: int | None = None
    turn_id: str | None = None
    current_cell_id: str | None = None
    outcome_summary: str = ""


DIRECTION_OFFSETS = {
    "NORTH": (0, 1),
    "SOUTH": (0, -1),
    "EAST": (1, 0),
    "WEST": (-1, 0),
}


class WorldSnapshot(Protocol):
    campaign: Mapping[str, Any]
    player: Mapping[str, Any]
    current_cell: Mapping[str, Any]
    destination_cell: Mapping[str, Any] | None
    characters: Sequence[Mapping[str, Any]]
    items: Sequence[Mapping[str, Any]]
    container_items: Sequence[Mapping[str, Any]]
    owned_items: Sequence[Mapping[str, Any]]


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return {_plain(item) for item in value}
    return value


def parse_cell_coords(cell_id: str) -> tuple[int, int]:
    """Parse 'cell_4_6' into (4, 6)."""
    parts = cell_id.split("_")
    return int(parts[1]), int(parts[2])


def format_cell_id(x: int, y: int) -> str:
    """Format (4, 6) into 'cell_4_6'."""
    return f"cell_{x}_{y}"


MAX_FEATURES_PER_CELL = 12

FEATURE_STATE_VALUES: Mapping[str, frozenset[str]] = {
    "open_state": frozenset({"open", "closed"}),
    "lock_state": frozenset({"locked", "unlocked"}),
    "condition": frozenset({"intact", "broken"}),
    "orientation": frozenset({"upright", "overturned"}),
    "light_state": frozenset({"lit", "unlit"}),
}

FEATURE_PROPERTIES = frozenset({
    "flammable", "breakable", "movable", "heavy", "container", "concealing", "light_source",
})


def _feature_supports(feature: Mapping[str, Any], key: str) -> bool:
    """Property prerequisites for a state key (§4.12)."""
    properties = set(feature.get("properties", ()))
    if key == "orientation":
        return "movable" in properties
    if key == "condition":
        return "breakable" in properties
    if key == "light_state":
        return bool(properties & {"light_source", "flammable"})
    if key in {"open_state", "lock_state"}:
        return "container" in properties or str(feature.get("kind", "")).casefold() == "door"
    return False


def find_feature(features: Sequence[Mapping[str, Any]], query: str) -> Mapping[str, Any] | None:
    """Match a feature in the cell by id, name, or kind; ambiguity is no match."""
    wanted = query.strip().casefold()
    for field_name in ("feature_id", "name", "kind"):
        matches = [feature for feature in features
                   if str(feature.get(field_name, "")).casefold() == wanted]
        if len(matches) == 1:
            return matches[0]
        if matches:
            return None
    return None


def validate_feature_state_change(
    features: Sequence[Mapping[str, Any]], effect: Mapping[str, Any]
) -> tuple[Mapping[str, Any] | None, str | None]:
    """Return the target feature, or a player-facing reason the change cannot happen."""
    feature = find_feature(features, str(effect.get("feature_id", "")))
    if feature is None:
        return None, "You don't see that here."
    key, value = str(effect.get("key", "")), str(effect.get("value", ""))
    name = feature.get("name", "it")
    if value not in FEATURE_STATE_VALUES.get(key, ()):
        return None, f"You can't do that to the {name}."
    if not _feature_supports(feature, key):
        return None, f"The {name} can't be made {value}."
    if feature.get("state", {}).get(key) == value:
        return None, f"The {name} is already {value}."
    return feature, None


def validate_created_feature(
    features: Sequence[Mapping[str, Any]], effect: Mapping[str, Any]
) -> str | None:
    """Return a player-facing reason a CREATE_FEATURE effect is invalid, else None."""
    if len(features) >= MAX_FEATURES_PER_CELL:
        return "There is no room left here for anything more."
    name = str(effect.get("name", "")).strip()
    if not name or len(name) > 40:
        return "That doesn't leave a mark worth noting."
    if not set(effect.get("properties", ())).issubset(FEATURE_PROPERTIES):
        return "That doesn't leave a mark worth noting."
    state = {key: value for key, value in dict(effect.get("state") or {}).items()
             if value is not None}
    if any(value not in FEATURE_STATE_VALUES.get(key, ()) for key, value in state.items()):
        return "That doesn't leave a mark worth noting."
    return None


def resolve_action(
    intent: ActionIntent,
    topology: Topology,
    current_cell_id: str,
    campaign_id: str,
    turn_sequence: int,
    turn_id: str,
) -> Resolution:
    """Resolve an action deterministically against the world state.

    Args:
        intent: The parsed (or adjudicated) action to resolve.
        topology: Cell connectivity graph.
        current_cell_id: The actor's current cell.
        campaign_id: Owning campaign (for event IDs).
        turn_sequence: 1-based turn number.
        turn_id: Client-generated idempotency key.
    """
    actor_id = intent.actor_id

    if intent.action_type == ActionType.MOVE:
        direction = intent.params.get("direction")
        if direction not in DIRECTION_OFFSETS:
            return Resolution(accepted=False, reason=f"Invalid direction '{direction}'")

        cur_x, cur_y = parse_cell_coords(current_cell_id)
        dx, dy = DIRECTION_OFFSETS[direction]
        target_cell_id = format_cell_id(cur_x + dx, cur_y + dy)

        if not topology.is_adjacent(current_cell_id, target_cell_id):
            return Resolution(
                accepted=False,
                reason=f"A wall blocks the way to the {direction.lower()}.",
            )

        event = Event(
            campaign_id=campaign_id,
            event_id=f"evt_{turn_sequence}_0",
            turn_sequence=turn_sequence,
            event_index=0,
            turn_id=turn_id,
            type=EventType.PLAYER_MOVED,
            actor_id=actor_id,
            entity_ids=[actor_id],
            cell_id=target_cell_id,
            payload={
                "from_cell": current_cell_id,
                "to_cell": target_cell_id,
                "direction": direction,
            },
            summary=f"Player moved {direction.lower()} to {target_cell_id}.",
            memory_status=MemoryStatus.NOT_REQUIRED,
        )

        return Resolution(
            accepted=True,
            events=[event],
            state_updates={
                "player_location": {"kind": "CELL", "ref_id": target_cell_id, "slot": None},
                "discovered_cell": target_cell_id,
            },
            outcome_summary=f"Moved {direction.lower()} into {target_cell_id}.",
        )

    if intent.action_type == ActionType.LOOK:
        return Resolution(
            accepted=True,
            outcome_summary=f"Looking around {current_cell_id}.",
        )

    if intent.action_type == ActionType.WAIT:
        return Resolution(
            accepted=True,
            outcome_summary="You wait a moment.",
        )

    return Resolution(accepted=False, reason=f"Unsupported action {intent.action_type}")


def resolve_inventory_action(
    intent: ActionIntent,
    *,
    items: Sequence[Mapping[str, Any]],
    player: Mapping[str, Any],
    current_cell_id: str,
    campaign_id: str,
    turn_sequence: int,
    turn_id: str,
    active_guard_ids: Sequence[str] = (),
    open_container_ids: Sequence[str] = (),
    lootable_owner_ids: Sequence[str] = (),
    new_item_id: str | None = None,
) -> Resolution:
    """Resolve an A3 inventory intent against supplied immutable snapshots."""
    query = str(intent.params.get("item_id") or intent.params.get("query") or "")
    normalized_query = query.casefold().strip()
    exact_matches = [
        item for item in items
        if item.get("entity_id") == query
        or str(item.get("name", "")).casefold() == normalized_query
    ]
    # A player should not need to type generated suffixes such as "weapon 1"
    # when a shorter description identifies exactly one visible item.  Prefer
    # exact matches, and only fall back to partial matching when unambiguous.
    matches = exact_matches or [
        item for item in items
        if normalized_query
        and normalized_query in str(item.get("name", "")).casefold()
    ]
    if len(matches) != 1:
        return Resolution(False, "You don't see that item here.")
    item_id = matches[0]["entity_id"]
    common = {"items": items, "item_id": item_id, "player_id": intent.actor_id}
    if intent.action_type == ActionType.TAKE_ITEM:
        result = take_item(
            **common,
            current_cell_id=current_cell_id,
            quantity=int(intent.params["quantity"]) if "quantity" in intent.params else None,
            active_guard_ids=active_guard_ids,
            open_container_ids=open_container_ids,
            lootable_owner_ids=lootable_owner_ids,
            new_item_id=new_item_id,
        )
    elif intent.action_type == ActionType.DROP_ITEM:
        result = drop_item(
            **common,
            current_cell_id=current_cell_id,
            quantity=int(intent.params["quantity"]) if "quantity" in intent.params else None,
            new_item_id=new_item_id,
        )
    elif intent.action_type == ActionType.EQUIP:
        result = equip_item(**common)
    elif intent.action_type == ActionType.UNEQUIP:
        result = unequip_item(**common, current_cell_id=current_cell_id)
    elif intent.action_type == ActionType.USE_ITEM:
        character = player["character"]
        result = use_item(
            **common,
            current_mp=character["mp"],
            max_mp=character["max_mp"],
        )
    else:
        return Resolution(False, f"Unsupported inventory action {intent.action_type}")
    if not result.accepted:
        return Resolution(False, result.reason)

    events = [
        Event(
            campaign_id=campaign_id,
            event_id=f"evt_{turn_sequence}_{index}",
            turn_sequence=turn_sequence,
            event_index=index,
            turn_id=turn_id,
            type=EventType(event_type),
            actor_id=intent.actor_id,
            entity_ids=[intent.actor_id, item_id],
            cell_id=current_cell_id,
            payload={"item_id": item_id},
            summary=f"{event_type.replace('_', ' ').title()}: {item_id}.",
            memory_status=MemoryStatus.NOT_REQUIRED,
        )
        for index, event_type in enumerate(result.event_types)
    ]
    originals = {item["entity_id"]: item for item in items}
    changed_items = [
        item
        for item in result.items
        if item["entity_id"] not in originals
        or item.get("location") != originals[item["entity_id"]].get("location")
        or item.get("item") != originals[item["entity_id"]].get("item")
    ]
    updates: dict[str, Any] = {"item_documents": changed_items}
    if result.player_mp is not None:
        updates["player_mp"] = result.player_mp
    return Resolution(True, events=events, state_updates=updates,
                      outcome_summary=events[-1].summary if events else "Inventory updated.")


def resolve_world_action(
    intent: ActionIntent,
    view: WorldSnapshot,
    *,
    turn_id: str,
) -> Resolution:
    """Resolve A4 combat, movement, waiting, and boss-door actions."""
    # Adjudicated intents name entities by ID in `targets`; fast-path intents use `query`.
    if intent.targets and "query" not in intent.params:
        fallback: dict[str, str | int] = {"query": intent.targets[0]}
        if intent.action_type == ActionType.STEAL and len(intent.targets) > 1:
            fallback["target_query"] = intent.targets[1]
        intent = intent.model_copy(update={"params": {**fallback, **intent.params}})
    campaign = _plain(view.campaign)
    player = _plain(view.player)
    current_cell = _plain(view.current_cell)
    destination = _plain(view.destination_cell) if view.destination_cell else None
    characters = [_plain(entity) for entity in view.characters]
    all_items = [
        *(_plain(item) for item in view.items),
        *(_plain(item) for item in view.container_items),
        *(_plain(item) for item in view.owned_items),
    ]
    # Queries can overlap view components; identity is authoritative.
    all_items = list({item["entity_id"]: item for item in all_items}.values())
    original_campaign = _plain(view.campaign)
    original_player = _plain(view.player)
    original_characters = {entity["entity_id"]: _plain(entity) for entity in view.characters}
    original_items = {item["entity_id"]: _plain(item) for item in all_items}
    sequence = int(campaign["current_turn"]) + 1
    rng = TurnRng(int(campaign["seed"]), sequence)
    events: list[Event] = []
    touched_cells: set[str] = set()
    inserted_items: list[dict[str, Any]] = []
    rejected_effects: list[dict[str, Any]] = []
    current_cell_changed = False

    def event(
        event_type: EventType,
        actor_id: str,
        entity_ids: list[str],
        cell_id: str,
        payload: Mapping[str, Any],
        summary: str,
        *,
        memory: MemoryStatus = MemoryStatus.NOT_REQUIRED,
    ) -> None:
        events.append(
            Event(
                campaign_id=campaign["_id"],
                event_id=f"evt_{sequence}_{len(events)}",
                turn_sequence=sequence,
                event_index=len(events),
                turn_id=turn_id,
                type=event_type,
                actor_id=actor_id,
                entity_ids=entity_ids,
                cell_id=cell_id,
                payload=dict(payload),
                summary=summary,
                memory_status=memory,
            )
        )

    def label(entity_id: str) -> str:
        if entity_id == player["entity_id"]:
            return "you"
        for entity in (*characters, *all_items):
            if entity.get("entity_id") == entity_id and entity.get("name"):
                return str(entity["name"])
        return entity_id

    def attack(attacker: dict[str, Any], defender: dict[str, Any], purpose: str) -> bool:
        outcome = resolve_attack(attacker, defender, items=all_items, rng=rng, purpose=purpose)
        defender["character"]["hp"] = outcome.hp_after
        if defender.get("entity_type") != "PLAYER" and outcome.killed:
            defender["character"]["status"] = "DEAD"
        payload = {
            "attacker_id": outcome.attacker_id,
            "defender_id": outcome.defender_id,
            "attacker_name": label(outcome.attacker_id),
            "defender_name": label(outcome.defender_id),
            "dodged": outcome.dodged,
            "damage": outcome.damage,
            "hp_before": outcome.hp_before,
            "hp_after": outcome.hp_after,
            "effective_dodge": outcome.effective_dodge,
            "weapon_bonus": outcome.weapon_bonus,
            "armor_bonus": outcome.armor_bonus,
            "variance": outcome.variance,
            "rolls": [record.__dict__ for record in outcome.rolls],
        }
        event(
            EventType.ATTACK_RESOLVED,
            attacker["entity_id"],
            [attacker["entity_id"], defender["entity_id"]],
            defender["location"]["ref_id"],
            payload,
            f"{label(attacker['entity_id'])} struck {label(defender['entity_id'])} for {outcome.damage} damage.",
            memory=MemoryStatus.PENDING,
        )
        return outcome.killed

    def apply_death(killer_ids: Sequence[str], death_cell: str) -> None:
        nonlocal player, all_items, inserted_items
        living_ids = [
            entity["entity_id"]
            for entity in hostile_order(characters, player["entity_id"], turn_sequence=sequence)
            if entity["location"]["ref_id"] == death_cell
        ]
        outcome = resolve_player_death(
            player,
            all_items,
            death_cell_id=death_cell,
            living_hostile_ids=living_ids,
            rng=rng,
            turn_sequence=sequence,
        )
        player = outcome.player
        all_items = list(outcome.items)
        inserted_items = [item for item in all_items if item["entity_id"] not in original_items]
        killer_names = [label(killer_id) for killer_id in killer_ids]
        slain_by = killer_names[-1] if killer_names else "the dungeon"
        event(EventType.PLAYER_DIED, player["entity_id"], [player["entity_id"], *killer_ids],
              death_cell, {"killer_ids": list(killer_ids), "killer_names": killer_names,
                           "death_cell": death_cell},
              f"You are slain by {slain_by}.", memory=MemoryStatus.PENDING)
        if outcome.dropped_item_id:
            item_name = label(outcome.dropped_item_id)
            event(EventType.ITEM_DROPPED, player["entity_id"],
                  [player["entity_id"], outcome.dropped_item_id], death_cell,
                  {"item_id": outcome.dropped_item_id, "item_name": item_name, "quantity": 1,
                   "guarded_by": living_ids},
                  f"You drop {item_name}.")
        event(EventType.XP_GAINED, player["entity_id"], [player["entity_id"]], death_cell,
              {"amount": outcome.death_xp, "reason": "DEATH", "pct": outcome.death_xp_pct,
               "exploration_pct": outcome.exploration_pct, "combat_pct": outcome.combat_pct},
              f"You gain {outcome.death_xp} XP.")
        event(EventType.PLAYER_RESPAWNED, player["entity_id"], [player["entity_id"]],
              player["location"]["ref_id"], {"spawn_cell_id": player["location"]["ref_id"]},
              "You wake back at the entrance.")

    def environment_response(cell_id: str, *, entering: bool = False) -> bool:
        killers: list[str] = []
        ordered = [entity for entity in hostile_order(characters, player["entity_id"],
                                                       turn_sequence=sequence)
                   if entity["location"]["ref_id"] == cell_id]
        if entering:
            ordered = [entity for entity in ordered
                       if entity["character"]["speed"] > player["character"]["speed"]]
        for hostile in ordered:
            killed = attack(hostile, player, "env")
            if killed:
                killers.append(hostile["entity_id"])
                apply_death(killers, cell_id)
                return True
        return False

    def find_character(query: str, *, npc_only: bool = False) -> dict[str, Any] | None:
        matches = [entity for entity in characters
                   if entity["location"]["ref_id"] == current_id
                   and entity["character"]["status"] == "ALIVE"
                   and (not npc_only or entity["entity_type"] == "NPC")
                   and (entity["entity_id"] == query
                        or entity.get("name", "").casefold() == query.casefold())]
        return matches[0] if len(matches) == 1 else None

    def disposition_for(npc: dict[str, Any]) -> tuple[DispositionState, int]:
        current = npc["character"].get("disposition", {}).get(player["entity_id"], {})
        trust = int(current.get("trust", 0))
        if current.get("state"):
            return DispositionState(current["state"]), trust
        # Enemies and the boss are hostile until a disposition is actually stored.
        if npc.get("entity_type") in {"ENEMY", "BOSS"}:
            return DispositionState.HOSTILE, trust
        return DispositionState.NEUTRAL, trust

    def change_disposition(
        npc: dict[str, Any], delta: int, *, reason_event_index: int | None = None
    ) -> None:
        state, trust = disposition_for(npc)
        outcome = adjust_disposition(state, trust, delta)
        current = npc["character"].setdefault("disposition", {}).get(player["entity_id"], {})
        source_index = len(events) - 1 if reason_event_index is None else reason_event_index
        reasons = [*current.get("reason_event_ids", ()), f"evt_{sequence}_{source_index}"][-10:]
        npc["character"]["disposition"][player["entity_id"]] = {
            "state": outcome.state_after.value,
            "trust": outcome.trust_after,
            "reason_event_ids": reasons,
        }
        events[source_index].payload["disposition"] = {
            "trust_before": outcome.trust_before,
            "trust_after": outcome.trust_after,
            "applied_delta": outcome.applied_delta,
        }
        if outcome.state_changed:
            event(EventType.DISPOSITION_CHANGED, player["entity_id"],
                  [player["entity_id"], npc["entity_id"]], current_id,
                  {"before": outcome.state_before.value, "after": outcome.state_after.value,
                   "trust": outcome.trust_after},
                  f"{npc['entity_id']} became {outcome.state_after.value.lower()}.",
                  memory=MemoryStatus.PENDING)

    current_id = current_cell["cell_id"]
    action = intent.action_type
    if action == ActionType.LOOK:
        pass
    elif action == ActionType.WAIT and intent.params.get("debug_die"):
        # Debug-only: the orchestrator sets this flag solely when DEBUG_ENDPOINTS is on.
        player["character"]["hp"] = 0
        apply_death([], current_id)
    elif action == ActionType.WAIT and intent.params.get("debug_reanimate"):
        # Debug-only. Restores one fallen creature in this room to full health.
        query = str(intent.params.get("query") or "").strip()
        here = [entity for entity in characters
                if entity["location"]["ref_id"] == current_id
                and entity["entity_id"] != player["entity_id"]]

        def named(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
            exact = [entity for entity in pool
                     if entity["entity_id"] == query
                     or entity.get("name", "").casefold() == query.casefold()]
            needle = query.casefold()
            return exact or [entity for entity in pool
                             if needle and needle in entity.get("name", "").casefold()]

        fallen = named([entity for entity in here if entity["character"]["status"] == "DEAD"])
        if len(fallen) > 1:
            return Resolution(False, "More than one fallen creature matches that name.")
        if not fallen:
            standing = named(
                [entity for entity in here if entity["character"]["status"] == "ALIVE"]
            )
            if len(standing) == 1:
                return Resolution(False, f"{label(standing[0]['entity_id'])} is already alive.")
            return Resolution(False, "There's no fallen creature here by that name.")
        target = fallen[0]
        max_hp = int(target["character"]["max_hp"])
        target["character"]["hp"] = max_hp
        target["character"]["status"] = "ALIVE"
        name = label(target["entity_id"])
        event(
            EventType.ENTITY_REANIMATED,
            player["entity_id"],
            [player["entity_id"], target["entity_id"]],
            current_id,
            {"entity_id": target["entity_id"], "hp_after": max_hp},
            f"{name} rises, restored to full health.",
            memory=MemoryStatus.NOT_REQUIRED,
        )
    elif action == ActionType.WAIT:
        environment_response(current_id)
    elif action == ActionType.SEARCH:
        hidden = [item for item in all_items if item.get("item", {}).get("hidden")
                  and item.get("location", {}).get("ref_id") == current_id]
        if not hidden:
            return Resolution(False, "There is nothing concealed to search for.")
        for item in hidden:
            outcome = resolve_check(CheckKind.SEARCH, player, rng=rng,
                                    suggested_difficulty=item["item"].get("concealment_dc", 12))
            event(EventType.CHECK_RESOLVED, player["entity_id"],
                  [player["entity_id"], item["entity_id"]], current_id,
                  {"kind": "SEARCH", "roll": outcome.roll, "total": outcome.total,
                   "dc": outcome.dc, "success": outcome.success,
                   "rolls": [record.__dict__ for record in outcome.rolls]},
                  f"Search {'succeeded' if outcome.success else 'failed'} for {item['entity_id']}.")
            if outcome.success:
                item["item"]["hidden"] = False
        environment_response(current_id)
    elif action == ActionType.TALK:
        # Any living character in the room, including enemies. A hostile one
        # still answers; fact revelation below stays closed while they are hostile.
        npc = find_character(str(intent.params.get("query", "")))
        if npc is None:
            return Resolution(False, "There's no one here by that name to speak with.")
        dialogue: dict[str, Any] = {"npc_id": npc["entity_id"]}
        if intent.params.get("utterance"):
            dialogue["utterance"] = str(intent.params["utterance"])
        event(EventType.DIALOGUE, player["entity_id"], [player["entity_id"], npc["entity_id"]],
              current_id, dialogue, f"Talked with {npc['entity_id']}.",
              memory=MemoryStatus.PENDING)
        state, trust = disposition_for(npc)
        if state != DispositionState.HOSTILE and trust >= 20:
            fact = next((fact for fact in npc["character"].get("knowledge", ())
                         if player["entity_id"] not in fact.get("revealed_to", ())), None)
            if fact is not None:
                fact.setdefault("revealed_to", []).append(player["entity_id"])
                rumored = fact["subject_cell_id"]
                if rumored not in player["player"]["rumored_cell_ids"]:
                    player["player"]["rumored_cell_ids"].append(rumored)
                event(EventType.FACT_REVEALED, npc["entity_id"],
                      [npc["entity_id"], player["entity_id"]], current_id,
                      {"fact_id": fact["fact_id"], "subject_cell_id": rumored},
                      fact["hint"], memory=MemoryStatus.PENDING)
                event(EventType.CELL_RUMORED, npc["entity_id"], [player["entity_id"]],
                      current_id, {"cell_id": rumored}, f"Rumored {rumored}.")
        environment_response(current_id)
    elif action in {ActionType.PERSUADE, ActionType.DECEIVE, ActionType.INTIMIDATE}:
        npc = find_character(str(intent.params.get("query", "")))
        if npc is None:
            return Resolution(False, "There's no one here by that name to speak with.")
        kind = {ActionType.PERSUADE: CheckKind.PERSUADE,
                ActionType.DECEIVE: CheckKind.DECEIVE,
                ActionType.INTIMIDATE: CheckKind.INTIMIDATE}[action]
        state, _trust = disposition_for(npc)
        outcome = resolve_check(kind, player, target=npc, rng=rng, disposition=state,
                                approach_modifier=int(intent.params.get("approach_modifier", 0)))
        check_payload: dict[str, Any] = {
            "kind": kind.value, "roll": outcome.roll, "total": outcome.total,
            "dc": outcome.dc, "success": outcome.success, "npc_id": npc["entity_id"],
            "rolls": [record.__dict__ for record in outcome.rolls],
        }
        if intent.params.get("utterance"):
            check_payload["utterance"] = str(intent.params["utterance"])
        event(EventType.CHECK_RESOLVED, player["entity_id"],
              [player["entity_id"], npc["entity_id"]], current_id, check_payload,
              f"{kind.value.title()} {'succeeded' if outcome.success else 'failed'}.")
        deltas = {
            ActionType.PERSUADE: (10, -5), ActionType.DECEIVE: (10, -20),
            ActionType.INTIMIDATE: (-15, -25),
        }
        change_disposition(npc, deltas[action][0 if outcome.success else 1])
        environment_response(current_id)
    elif action == ActionType.STEAL:
        target = find_character(str(intent.params.get("target_query", "")))
        query = str(intent.params.get("query", ""))
        stolen = next((item for item in all_items
                       if target is not None and item.get("location", {}).get("kind") == "INVENTORY"
                       and item["location"].get("ref_id") == target["entity_id"]
                       and (item["entity_id"] == query
                            or item.get("name", "").casefold() == query.casefold())), None)
        if target is None or stolen is None:
            return Resolution(False, "You can't find that to steal.")
        state = disposition_for(target)[0] if target["entity_type"] == "NPC" else DispositionState.HOSTILE
        outcome = resolve_check(CheckKind.STEAL, player, target=target, rng=rng, disposition=state)
        event(EventType.CHECK_RESOLVED, player["entity_id"],
              [player["entity_id"], target["entity_id"], stolen["entity_id"]], current_id,
              {"kind": "STEAL", "roll": outcome.roll, "total": outcome.total,
               "dc": outcome.dc, "success": outcome.success,
               "rolls": [record.__dict__ for record in outcome.rolls]},
              f"Steal {'succeeded' if outcome.success else 'failed'}.")
        check_event_index = len(events) - 1
        if outcome.success:
            transfer = take_item(all_items, item_id=stolen["entity_id"],
                                 player_id=player["entity_id"], current_cell_id=current_id,
                                 lootable_owner_ids=[target["entity_id"]],
                                 new_item_id=f"item_split_{sequence}")
            if not transfer.accepted:
                return Resolution(False, transfer.reason)
            all_items = list(transfer.items)
            event(EventType.ITEM_TRANSFERRED, player["entity_id"],
                  [player["entity_id"], target["entity_id"], stolen["entity_id"]], current_id,
                  {"item_id": stolen["entity_id"], "from": target["entity_id"]},
                  f"Stole {stolen['entity_id']}.")
        else:
            target["character"]["alerted"] = True
        if target["entity_type"] == "NPC":
            change_disposition(
                target,
                -20 if outcome.success else -40,
                reason_event_index=check_event_index,
            )
        environment_response(current_id)
    elif action == ActionType.ATTACK and intent.params.get("debug_murder"):
        # Debug-only. The orchestrator sets this flag when DEBUG_ENDPOINTS is on.
        query = str(intent.params.get("query") or "").strip()
        living = [entity for entity in characters
                  if entity["location"]["ref_id"] == current_id
                  and entity["character"]["status"] == "ALIVE"
                  and entity["entity_id"] != player["entity_id"]]
        exact = [entity for entity in living
                 if entity["entity_id"] == query
                 or entity.get("name", "").casefold() == query.casefold()]
        needle = query.casefold()
        matches = exact or [entity for entity in living
                            if needle and needle in entity.get("name", "").casefold()]
        if len(matches) != 1:
            reason = ("More than one creature matches that name." if len(matches) > 1
                      else "There's nothing here by that name to murder.")
            return Resolution(False, reason)
        target = matches[0]
        hp_before = int(target["character"]["hp"])
        target["character"]["hp"] = 0
        target["character"]["status"] = "DEAD"
        name = label(target["entity_id"])
        event(
            EventType.ATTACK_RESOLVED,
            player["entity_id"],
            [player["entity_id"], target["entity_id"]],
            current_id,
            {
                "attacker_id": player["entity_id"],
                "defender_id": target["entity_id"],
                "attacker_name": "you",
                "defender_name": name,
                "dodged": False,
                "damage": hp_before,
                "hp_before": hp_before,
                "hp_after": 0,
                "effective_dodge": 0,
                "weapon_bonus": 0,
                "armor_bonus": 0,
                "variance": 0,
                "rolls": [],
            },
            f"You slay {name}.",
            memory=MemoryStatus.PENDING,
        )
        event(EventType.ENTITY_DIED, player["entity_id"],
              [player["entity_id"], target["entity_id"]], current_id,
              {"entity_id": target["entity_id"]}, f"{name} died.",
              memory=MemoryStatus.PENDING)
        xp = kill_xp(player["character"]["level"], target["character"]["level"],
                     boss=target["entity_type"] == "BOSS")
        award = award_xp(player["character"], xp)
        player["character"]["xp"] = award.xp_after
        player["character"]["pending_level_ups"] = award.pending_level_ups_after
        player["player"]["kills"] += 1
        player["player"]["damage_dealt_since_death"] += hp_before
        event(EventType.XP_GAINED, player["entity_id"],
              [player["entity_id"], target["entity_id"]], current_id,
              {"amount": xp, "reason": "KILL", "target_id": target["entity_id"]},
              f"You gain {xp} XP.")
        environment_response(current_id)
    elif action == ActionType.ATTACK:
        query = str(intent.params.get("target_id") or intent.params.get("query") or "")
        candidates = [entity for entity in characters
                      if entity["location"]["ref_id"] == current_id
                      and entity["character"]["status"] == "ALIVE"
                      and entity["entity_id"] != player["entity_id"]
                      and (entity["entity_id"] == query
                           or entity.get("name", "").casefold() == query.casefold())]
        if len(candidates) != 1:
            return Resolution(False, "There's nothing here by that name to attack.")
        target = candidates[0]
        disposition_change: tuple[str, str, int, int] | None = None
        if target["entity_type"] == "NPC":
            dispositions = target["character"].setdefault("disposition", {})
            previous = dispositions.get(player["entity_id"], {
                "state": "NEUTRAL", "trust": 0, "reason_event_ids": []
            })
            before_state = previous.get("state", "NEUTRAL")
            before_trust = int(previous.get("trust", 0))
            after_trust = max(-100, before_trust - 60)
            after_state = before_state
            if after_trust <= -50:
                after_state = "HOSTILE"
            elif before_state == "NEUTRAL" and after_trust < -20:
                after_state = "WARY"
            elif before_state == "FRIENDLY" and after_trust < 20:
                after_state = "NEUTRAL"
            reason_ids = list(previous.get("reason_event_ids", ()))
            reason_ids.append(f"evt_{sequence}_{len(events)}")
            dispositions[player["entity_id"]] = {
                "state": after_state,
                "trust": after_trust,
                "reason_event_ids": reason_ids[-10:],
            }
            disposition_change = (before_state, after_state, before_trust, after_trust)
        killed = attack(player, target, "combat")
        dealt = events[-1].payload["damage"]
        if disposition_change is not None:
            before_state, after_state, before_trust, after_trust = disposition_change
            events[-1].payload["disposition"] = {
                "trust_before": before_trust,
                "trust_after": after_trust,
            }
            if before_state != after_state:
                event(EventType.DISPOSITION_CHANGED, player["entity_id"],
                      [player["entity_id"], target["entity_id"]], current_id,
                      {"entity_id": target["entity_id"], "before": before_state,
                       "after": after_state, "trust": after_trust},
                      f"{target['entity_id']} became {after_state.lower()}.",
                      memory=MemoryStatus.PENDING)
        player["player"]["damage_dealt_since_death"] += dealt
        if killed:
            event(EventType.ENTITY_DIED, player["entity_id"],
                  [player["entity_id"], target["entity_id"]], current_id,
                  {"entity_id": target["entity_id"]}, f"{target['entity_id']} died.",
                  memory=MemoryStatus.PENDING)
            xp = kill_xp(player["character"]["level"], target["character"]["level"],
                         boss=target["entity_type"] == "BOSS")
            award = award_xp(player["character"], xp)
            player["character"]["xp"] = award.xp_after
            player["character"]["pending_level_ups"] = award.pending_level_ups_after
            player["player"]["kills"] += 1
            event(EventType.XP_GAINED, player["entity_id"],
                  [player["entity_id"], target["entity_id"]], current_id,
                  {"amount": xp, "reason": "KILL", "target_id": target["entity_id"]},
                  f"{player['entity_id']} gained {xp} XP.")
        environment_response(current_id)
    elif action in {ActionType.MOVE, ActionType.FLEE}:
        direction = intent.params.get("direction")
        if direction not in DIRECTION_OFFSETS:
            return Resolution(False, f"Invalid direction '{direction}'")
        x, y = parse_cell_coords(current_id)
        dx, dy = DIRECTION_OFFSETS[str(direction)]
        target_id = format_cell_id(x + dx, y + dy)
        topology = Topology(campaign["topology"])
        if not topology.is_adjacent(current_id, target_id):
            return Resolution(False, f"A wall blocks the way to the {str(direction).lower()}.")
        if destination is None or destination["cell_id"] != target_id:
            return Resolution(False, "Destination snapshot is required before movement.")
        if not can_enter_boss(campaign, target_id):
            return Resolution(False, "The sealed boss door blocks the way.")
        source_hostiles = [entity for entity in hostile_order(characters, player["entity_id"],
                                                               turn_sequence=sequence)
                           if entity["location"]["ref_id"] == current_id]
        if action == ActionType.FLEE or source_hostiles:
            if source_hostiles and attack(source_hostiles[0], player, "env"):
                apply_death([source_hostiles[0]["entity_id"]], current_id)
            else:
                player["location"] = {"kind": "CELL", "ref_id": target_id, "slot": None}
        else:
            player["location"] = {"kind": "CELL", "ref_id": target_id, "slot": None}
        if player["location"]["ref_id"] == target_id:
            discovered = target_id not in player["player"]["discovered_cell_ids"]
            if discovered:
                player["player"]["discovered_cell_ids"].append(target_id)
                player["player"]["new_cells_since_death"] += 1
            destination["visited_by"] = sorted(set(destination.get("visited_by", ())) | {player["entity_id"]})
            destination["last_updated_turn"] = sequence
            touched_cells.add(target_id)
            event(EventType.PLAYER_MOVED, player["entity_id"], [player["entity_id"]], target_id,
                  {"from_cell": current_id, "to_cell": target_id, "direction": direction},
                  f"Player moved {str(direction).lower()} to {target_id}.")
            if discovered:
                event(EventType.CELL_DISCOVERED, player["entity_id"], [player["entity_id"]],
                      target_id, {"cell_id": target_id}, f"Discovered {target_id}.")
            environment_response(target_id, entering=True)
    elif action in {
        ActionType.TAKE_ITEM,
        ActionType.DROP_ITEM,
        ActionType.EQUIP,
        ActionType.UNEQUIP,
        ActionType.USE_ITEM,
    }:
        active_guards = [entity["entity_id"] for entity in characters
                         if entity["character"]["status"] == "ALIVE"]
        lootable_owners = [entity["entity_id"] for entity in characters
                           if entity["character"]["status"] in {"DEAD", "INCAPACITATED"}]
        open_containers = [feature["feature_id"] for feature in current_cell.get("features", ())
                           if feature.get("state", {}).get("open_state") == "open"]
        query = str(intent.params.get("item_id") or intent.params.get("query") or "")
        target = next((item for item in all_items
                       if item["entity_id"] == query
                       or item.get("name", "").casefold() == query.casefold()), None)
        if target and action == ActionType.TAKE_ITEM and target["item"].get("subtype") == "TREASURE":
            boss_alive = any(entity["entity_type"] == "BOSS"
                             and entity["character"]["status"] == "ALIVE"
                             for entity in characters)
            if boss_alive:
                return Resolution(False, "The treasure remains guarded while the boss lives.")
        inventory_resolution = resolve_inventory_action(
            intent,
            items=all_items,
            player=player,
            current_cell_id=current_id,
            campaign_id=campaign["_id"],
            turn_sequence=sequence,
            turn_id=turn_id,
            active_guard_ids=active_guards,
            open_container_ids=open_containers,
            lootable_owner_ids=lootable_owners,
            new_item_id=f"item_split_{sequence}",
        )
        if not inventory_resolution.accepted:
            return inventory_resolution
        all_items = inventory_resolution.state_updates["item_documents"] + [
            item for item in all_items
            if item["entity_id"] not in {
                changed["entity_id"]
                for changed in inventory_resolution.state_updates["item_documents"]
            }
        ]
        if "player_mp" in inventory_resolution.state_updates:
            player["character"]["mp"] = inventory_resolution.state_updates["player_mp"]
        events.extend(inventory_resolution.events)
        if target and action == ActionType.TAKE_ITEM and target["item"].get("subtype") == "TREASURE":
            campaign = claim_treasure(campaign, player_id=player["entity_id"], boss_alive=False)
            event(EventType.TREASURE_CLAIMED, player["entity_id"],
                  [player["entity_id"], target["entity_id"]], current_id,
                  {"treasure_id": target["entity_id"]}, "The boss treasure was claimed.")
        environment_response(current_id)
    elif action == ActionType.INTERACT and "boss_door" in {
        str(intent.params.get("query", "")).replace(" ", "_"), *intent.targets
    }:
        topology = Topology(campaign["topology"])
        if campaign["boss_cell_id"] not in topology.neighbors(current_id):
            return Resolution(False, "The boss door is not reachable from here.")
        outcome = submit_keys(campaign, all_items, player_id=player["entity_id"])
        if not outcome.submitted_item_ids:
            return Resolution(False, "You carry no unsubmitted keys.")
        campaign = outcome.campaign
        all_items = list(outcome.items)
        event(EventType.KEYS_SUBMITTED, player["entity_id"],
              [player["entity_id"], *outcome.submitted_item_ids], current_id,
              {"key_item_ids": list(outcome.submitted_item_ids)},
              f"Submitted {len(outcome.submitted_item_ids)} keys.")
        if outcome.unlocked_now:
            event(EventType.BOSS_DOOR_UNLOCKED, player["entity_id"], [player["entity_id"]],
                  current_id, {"boss_cell_id": campaign["boss_cell_id"]},
                  "The boss door unlocked.")
        environment_response(current_id)
    elif action in {ActionType.INTERACT, ActionType.CREATIVE_INTERACTION}:
        effects = list(intent.effects_on_success)
        check_kind = intent.params.get("check_kind")
        if check_kind:
            kind = CheckKind(str(check_kind))
            if kind not in {CheckKind.SKILL, CheckKind.SEARCH}:
                kind = CheckKind.SKILL
            outcome = resolve_check(
                kind, player, rng=rng,
                approach_modifier=int(intent.params.get("approach_modifier", 0)),
                suggested_difficulty=int(intent.params.get("suggested_difficulty", 10)),
            )
            event(EventType.CHECK_RESOLVED, player["entity_id"], [player["entity_id"]],
                  current_id,
                  {"kind": kind.value, "roll": outcome.roll, "total": outcome.total,
                   "dc": outcome.dc, "success": outcome.success,
                   "rolls": [record.__dict__ for record in outcome.rolls]},
                  f"{kind.value.title()} check {'succeeded' if outcome.success else 'failed'}.")
            if not outcome.success:
                effects = list(intent.effects_on_failure)
        features = current_cell.setdefault("features", [])
        first_refusal: str | None = None
        for effect in effects:
            effect_type = effect.get("type")
            if effect_type == "SET_FEATURE_STATE":
                feature, refusal = validate_feature_state_change(features, effect)
                if feature is None:
                    rejected_effects.append(dict(effect))
                    first_refusal = first_refusal or refusal
                    continue
                key, value = str(effect["key"]), str(effect["value"])
                before = feature.get("state", {}).get(key)
                feature.setdefault("state", {})[key] = value  # type: ignore[union-attr]
                current_cell_changed = True
                event(EventType.FEATURE_STATE_CHANGED, player["entity_id"],
                      [player["entity_id"], feature["feature_id"]], current_id,
                      {"feature_id": feature["feature_id"], "key": key,
                       "before": before, "after": value},
                      f"The {feature['name']} is now {value}.",
                      memory=MemoryStatus.PENDING)
            elif effect_type == "CREATE_FEATURE":
                refusal = validate_created_feature(features, effect)
                if refusal is not None:
                    rejected_effects.append(dict(effect))
                    first_refusal = first_refusal or refusal
                    continue
                taken = {feature.get("feature_id") for feature in features}
                index = len(features) + 1
                while f"feat_{current_id}_{index}" in taken:
                    index += 1
                created = {
                    "feature_id": f"feat_{current_id}_{index}",
                    "kind": str(effect.get("kind", "mark")),
                    "name": str(effect["name"]).strip(),
                    "properties": list(effect.get("properties", ())),
                    "state": {key: value for key, value in dict(effect.get("state") or {}).items()
                              if value is not None},
                    "created_by": "PLAYER_ACTION",
                }
                features.append(created)
                current_cell_changed = True
                event(EventType.FEATURE_CREATED, player["entity_id"],
                      [player["entity_id"], created["feature_id"]], current_id,
                      {"feature": created}, f"Created {created['name']}.",
                      memory=MemoryStatus.PENDING)
            elif effect_type != "NOOP":
                rejected_effects.append(dict(effect))
        if not current_cell_changed and not check_kind:
            return Resolution(False, first_refusal or "Nothing you do there changes anything.",
                              rejected_effects=rejected_effects)
        environment_response(current_id)
    else:
        return Resolution(False, "That isn't something you can do right now.")

    mutations: list[DocumentMutation] = []
    campaign_fields = {key: campaign[key] for key in ("boss_door", "status", "winner_player_id")
                       if campaign.get(key) != original_campaign.get(key)}
    if campaign_fields:
        mutations.append(DocumentMutation("campaigns", campaign["_id"], campaign["version"],
                                          set_fields=campaign_fields))
    if player != original_player:
        mutations.append(DocumentMutation(
            "entities", player["entity_id"], original_player["version"],
            set_fields={"location": player["location"], "character": player["character"],
                        "player": player["player"], "updated_turn": sequence},
        ))
    for character in characters:
        original = original_characters[character["entity_id"]]
        if character != original:
            mutations.append(DocumentMutation(
                "entities", character["entity_id"], original["version"],
                set_fields={"character": character["character"], "updated_turn": sequence},
            ))
    for item in all_items:
        original = original_items.get(item["entity_id"])
        if original is not None and item != original:
            mutations.append(DocumentMutation(
                "entities", item["entity_id"], original["version"],
                set_fields={"location": item["location"], "item": item["item"],
                            "updated_turn": sequence},
            ))
    if destination is not None and destination["cell_id"] in touched_cells:
        mutations.append(DocumentMutation(
            "cells", destination["cell_id"], int(view.destination_cell["version"]),
            set_fields={"visited_by": destination["visited_by"],
                        "last_updated_turn": destination["last_updated_turn"]},
        ))
    if current_cell_changed:
        touched_cells.add(current_cell["cell_id"])
        mutations.append(DocumentMutation(
            "cells", current_cell["cell_id"], int(view.current_cell["version"]),
            set_fields={"features": current_cell["features"], "last_updated_turn": sequence},
        ))
    inserts = [DocumentInsert("entities", item) for item in inserted_items]
    return Resolution(
        True,
        events=events,
        mutations=mutations,
        inserts=inserts,
        touched_entity_ids=sorted({mutation.document_id for mutation in mutations
                                   if mutation.collection == "entities"}),
        touched_cell_ids=sorted(touched_cells),
        rejected_effects=rejected_effects,
        expected_turn=int(original_campaign["current_turn"]),
        expected_campaign_version=int(original_campaign["version"]),
        turn_id=turn_id,
        current_cell_id=player["location"]["ref_id"],
        outcome_summary=events[-1].summary if events else f"Looking around {current_id}.",
    )
