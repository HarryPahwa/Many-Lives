"""Mongo-backed implementation of the integration ``EnginePort`` seam."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import logging
from typing import Any

from app.api.schemas import (
    CampaignSummary,
    InventoryItem,
    MapCell,
    MapResponse,
    PlayerSheet,
    PlayerState,
    Roll,
    Stats,
    VisibleCell,
    VisibleCharacter,
    VisibleFeature,
    VisibleItem,
)
from app.domain.parser import parse_fast_path
from app.domain.rules import DIRECTION_OFFSETS, Resolution, resolve_world_action
from app.domain.types import ActionIntent
from app.persistence.mongo import get_database
from app.persistence.repositories import Repository, StateNotFoundError
from app.persistence.views import WorldView as PersistenceWorldView
from app.services.campaign_service import create_campaign
from app.services.room_service import generate_room
from app.world.topology import parse_cell_key


logger = logging.getLogger(__name__)


class MongoEngine:
    """Adapt deterministic domain and persistence services to ``EnginePort``."""

    DURABLE = True

    def __init__(self, repository: Repository | None = None) -> None:
        self._repository = repository
        self._indexes_ready = False

    @classmethod
    def from_environment(cls) -> "MongoEngine":
        return cls()

    @property
    def repository(self) -> Repository:
        if self._repository is None:
            self._repository = Repository(get_database())
        return self._repository

    def create_campaign(self, player_name: str, seed: int | None) -> CampaignSummary:
        self._ensure_indexes()
        created = create_campaign(self.repository, player_name, seed=seed)
        campaign = self.get_campaign(created.campaign_id)
        assert campaign is not None
        return campaign

    def list_campaigns(self) -> list[CampaignSummary]:
        summaries = []
        for campaign in self.repository.list_campaigns():
            try:
                summaries.append(self._summary(campaign))
            except StateNotFoundError:
                logger.warning("Skipping campaign with missing player: %s", campaign["_id"])
        return summaries

    def get_campaign(self, campaign_id: str) -> CampaignSummary | None:
        campaign = self.repository.get_campaign(campaign_id)
        return self._summary(campaign) if campaign is not None else None

    def load_world_view(self, campaign_id: str, player_id: str) -> WorldView:
        snapshot = self.repository.load_world_view(campaign_id, player_id)
        if not snapshot.current_cell.get("generated", False):
            generate_room(self.repository, campaign_id, snapshot.current_cell["cell_id"])
            snapshot = self.repository.load_world_view(campaign_id, player_id)
        return self._seam_view(snapshot)

    def parse_fast_path(self, text: str, actor_id: str) -> Intent | None:
        from app.services.stubs import Intent

        parsed = parse_fast_path(text, actor_id)
        if parsed is None:
            return None
        return Intent(
            action_type=parsed.action_type.value,
            actor_id=parsed.actor_id,
            targets=list(parsed.targets),
            params=dict(parsed.params),
            effects_on_success=list(parsed.effects_on_success),
        )

    def resolve(self, view: WorldView, intent: Intent) -> EngineResolution:
        from app.services.stubs import EngineResolution

        try:
            action = ActionIntent(
                action_type=intent.action_type,
                actor_id=intent.actor_id,
                targets=intent.targets,
                params=intent.params,
                effects_on_success=intent.effects_on_success,
                effects_on_failure=intent.effects_on_failure,
            )
            snapshot = self._snapshot_for_action(view.campaign_id, view.player_id, action)
        except (ValueError, StateNotFoundError) as exc:
            return EngineResolution(accepted=False, reason=str(exc))

        raw = resolve_world_action(action, snapshot, turn_id="pending")
        return self._seam_resolution(raw, pending=(snapshot, action))

    def commit_turn(
        self, view: WorldView, resolution: EngineResolution, turn_id: str
    ) -> CommitResult:
        from app.services.stubs import CommitResult

        pending = resolution.pending
        if not isinstance(pending, tuple) or len(pending) != 2:
            raise ValueError("Mongo resolutions must be committed by their originating engine")
        snapshot, action = pending
        raw = resolve_world_action(action, snapshot, turn_id=turn_id)
        if not raw.accepted:
            raise ValueError("Accepted resolution became rejected before commit")
        self.repository.commit_turn(
            view.campaign_id, view.player_id, raw, store_result=False
        )
        if raw.current_cell_id is not None:
            generate_room(self.repository, view.campaign_id, raw.current_cell_id)
        return CommitResult(
            turn_sequence=(raw.expected_turn or 0) + 1,
            event_ids=[event.event_id for event in raw.events],
            events=[event.model_dump(mode="json") for event in raw.events],
        )

    def generate_room(self, campaign_id: str, key: str) -> None:
        generate_room(self.repository, campaign_id, key)

    def build_map(self, campaign_id: str, player_id: str) -> MapResponse:
        snapshot = self.repository.load_world_view(campaign_id, player_id)
        campaign = snapshot.campaign
        player = snapshot.player
        discovered = set(player["player"].get("discovered_cell_ids", ()))
        rumored = set(player["player"].get("rumored_cell_ids", ())) - discovered
        cells = {
            cell["cell_id"]: cell
            for cell in self.repository.get_cells(campaign_id, sorted(discovered | rumored))
        }
        entries: list[MapCell] = []
        for cell_id in sorted(discovered):
            cell = cells[cell_id]
            x, y = parse_cell_key(cell_id)
            entries.append(
                MapCell(
                    cell_id=cell_id,
                    x=x,
                    y=y,
                    state="DISCOVERED",
                    exits=self._exits(campaign, cell_id),
                    name=(cell.get("room") or {}).get("name"),
                    boss=bool(cell.get("reservations", {}).get("boss")),
                )
            )
        for cell_id in sorted(rumored):
            cell = cells[cell_id]
            entries.append(
                MapCell(
                    cell_id=cell_id,
                    x=cell["x"],
                    y=cell["y"],
                    state="RUMORED",
                    boss=False,
                )
            )
        grid = campaign["config"]["grid"]
        return MapResponse(
            width=grid["width"],
            height=grid["height"],
            player_cell=player["location"]["ref_id"],
            cells=entries,
            rumored=sorted(rumored),
        )

    def player_sheet(self, campaign_id: str, player_id: str) -> PlayerSheet:
        snapshot = self.repository.load_world_view(campaign_id, player_id)
        player = snapshot.player
        character = player["character"]
        items = [
            item
            for item in snapshot.owned_items
            if item["location"].get("ref_id") == player_id
        ]
        carried = [
            self._inventory_item(item)
            for item in items
            if item["location"].get("kind") == "INVENTORY"
        ]
        equipped = {item["location"].get("slot"): self._inventory_item(item) for item in items
                    if item["location"].get("kind") == "EQUIPPED"}
        return PlayerSheet(
            player_id=player_id,
            name=player["name"],
            hp=character["hp"],
            max_hp=character["max_hp"],
            mp=character["mp"],
            max_mp=character["max_mp"],
            level=character["level"],
            xp=character["xp"],
            pending_level_ups=character["pending_level_ups"],
            cell_id=player["location"]["ref_id"],
            status=character["status"],
            stats=Stats(
                attack=character["attack"], defense=character["defense"],
                speed=character["speed"], dodge_pct=character["dodge_pct"],
                skill=character["skill"],
            ),
            carried=carried,
            weapon=equipped.get("WEAPON"),
            armor=equipped.get("ARMOR"),
            keys_held=sum(1 for item in carried if item.name.casefold().endswith("key")),
            keys_required=snapshot.campaign["config"]["keys_required"],
        )

    def history_stats(self, campaign_id: str) -> dict[str, int]:
        return self.repository.history_stats(campaign_id)

    def get_turn(self, campaign_id: str, turn_id: str) -> TurnRecord | None:
        document = self.repository.get_turn(campaign_id, turn_id)
        return self._turn_record(document) if document is not None else None

    def _ensure_indexes(self) -> None:
        if not self._indexes_ready:
            self.repository.ensure_indexes()
            self._indexes_ready = True

    def put_turn(self, record: TurnRecord) -> None:
        claim = self.repository.begin_turn(
            record.campaign_id, record.turn_id, record.player_id, record.input or "",
            kind=record.kind, path=record.path,
        )
        fields = asdict(record)
        fields.pop("campaign_id")
        fields.pop("turn_id")
        fields = {key: value for key, value in fields.items() if value is not None}
        fields.pop("created_at", None)
        self.repository.update_turn(record.campaign_id, record.turn_id, fields)

    def latest_turn(self, campaign_id: str) -> TurnRecord | None:
        document = self.repository.latest_turn(campaign_id)
        return self._turn_record(document) if document is not None else None

    def _snapshot_for_action(
        self, campaign_id: str, player_id: str, action: ActionIntent
    ) -> PersistenceWorldView:
        source = self.repository.load_world_view(campaign_id, player_id)
        destination: str | None = None
        if action.action_type.value in {"MOVE", "FLEE"}:
            direction = str(action.params.get("direction", ""))
            offset = DIRECTION_OFFSETS.get(direction)
            if offset is not None:
                x, y = parse_cell_key(source.current_cell["cell_id"])
                candidate = f"cell_{x + offset[0]}_{y + offset[1]}"
                if candidate in source.campaign["topology"].get(source.current_cell["cell_id"], ()):
                    destination = candidate
        if destination is None:
            return source
        return self.repository.load_world_view(campaign_id, player_id, destination)

    def _seam_view(self, snapshot: PersistenceWorldView) -> WorldView:
        from app.services.stubs import WorldView

        campaign = snapshot.campaign
        player = snapshot.player
        return WorldView(
            campaign_id=campaign["_id"],
            player_id=player["entity_id"],
            campaign_status=campaign["status"],
            current_turn=campaign["current_turn"],
            player=PlayerState(
                hp=player["character"]["hp"], max_hp=player["character"]["max_hp"],
                mp=player["character"]["mp"], max_mp=player["character"]["max_mp"],
                level=player["character"]["level"], xp=player["character"]["xp"],
                pending_level_ups=player["character"]["pending_level_ups"],
                cell_id=player["location"]["ref_id"],
            ),
            visible_cell=self._visible_cell(snapshot),
            exits=self._exits(campaign, snapshot.current_cell["cell_id"]),
        )

    @staticmethod
    def _visible_disposition(entity: dict[str, Any], player_id: str) -> str | None:
        stored = (entity["character"].get("disposition", {}).get(player_id) or {}).get("state")
        if stored:
            return stored
        if entity.get("entity_type") in {"ENEMY", "BOSS"}:
            return "HOSTILE"
        return None

    def _visible_cell(self, snapshot: PersistenceWorldView) -> VisibleCell:
        cell = snapshot.current_cell
        room = cell.get("room") or {}
        environment = room.get("static_environment") or {}
        description = " ".join(
            value for value in (environment.get("architectural_notes"), environment.get("lighting"),
                                environment.get("smell")) if value
        )
        player_id = snapshot.player["entity_id"]
        characters = [
            VisibleCharacter(
                id=entity["entity_id"], name=entity["name"],
                status=entity["character"]["status"],
                disposition=self._visible_disposition(entity, player_id),
            )
            for entity in snapshot.characters if entity["entity_id"] != player_id
            and entity["location"].get("ref_id") == cell["cell_id"]
        ]
        items = [
            VisibleItem(id=item["entity_id"], name=item["name"], where="floor")
            for item in snapshot.items if item["location"].get("ref_id") == cell["cell_id"]
        ]
        return VisibleCell(
            cell_id=cell["cell_id"], name=room.get("name") or "Unlit Passage",
            description=description,
            exits=self._exits(snapshot.campaign, cell["cell_id"]),
            features=[VisibleFeature(id=feature["feature_id"], name=feature["name"],
                                     state=dict(feature.get("state", {})))
                      for feature in cell.get("features", ())],
            characters=characters,
            items=items,
        )

    def _summary(self, campaign: dict[str, Any]) -> CampaignSummary:
        player_id = campaign["player_ids"][0]
        player = self.repository.get_entity(campaign["_id"], player_id)
        if player is None:
            raise StateNotFoundError(f"Player not found: {player_id}")
        return CampaignSummary(
            campaign_id=campaign["_id"], status=campaign["status"],
            current_turn=campaign["current_turn"], player_id=player_id,
            player_name=player["name"], updated_at=_timestamp(campaign["updated_at"]),
        )

    @staticmethod
    def _seam_resolution(raw: Resolution, *, pending: tuple[Any, ActionIntent]) -> EngineResolution:
        from app.services.stubs import EngineResolution

        rolls = [
            Roll(purpose=roll["purpose"], sides=roll["sides"], value=roll["value"])
            for event in raw.events for roll in event.payload.get("rolls", ())
            if isinstance(roll.get("sides"), int) and isinstance(roll.get("value"), int)
            and not isinstance(roll.get("value"), bool)
        ]
        return EngineResolution(
            accepted=raw.accepted, reason=raw.reason,
            effects=[{"type": event.type.value} for event in raw.events],
            event_types=[event.type.value for event in raw.events],
            outcome_summary=raw.outcome_summary, rolls=rolls,
            rejected_effects=list(raw.rejected_effects), pending=pending,
        )

    @staticmethod
    def _exits(campaign: dict[str, Any], cell_id: str) -> list[str]:
        x, y = parse_cell_key(cell_id)
        neighbors = set(campaign["topology"].get(cell_id, ()))
        directions = (("north", (0, 1)), ("south", (0, -1)),
                      ("east", (1, 0)), ("west", (-1, 0)))
        return [name for name, (dx, dy) in directions if f"cell_{x + dx}_{y + dy}" in neighbors]

    @staticmethod
    def _inventory_item(item: dict[str, Any]) -> InventoryItem:
        return InventoryItem(
            id=item["entity_id"], name=item["name"],
            quantity=item["item"].get("quantity", 1),
            slot=item["location"].get("slot"),
            stackable=item["item"].get("stackable", False),
        )

    @staticmethod
    def _turn_record(document: dict[str, Any]) -> TurnRecord:
        from app.services.stubs import TurnRecord

        allowed = TurnRecord.__dataclass_fields__
        fields = {
            key: value for key, value in document.items()
            if key != "_id" and key in allowed
        }
        for key in ("created_at", "committed_at", "narrated_at"):
            if fields.get(key) is not None:
                fields[key] = _timestamp(fields[key])
        return TurnRecord(**fields)


def _timestamp(value: datetime | str) -> str:
    return value.isoformat() if isinstance(value, datetime) else value
