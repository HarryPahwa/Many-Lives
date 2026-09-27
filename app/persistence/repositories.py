"""Campaign-scoped repositories and A2 commit paths (TDD §9.9)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Any, Literal, TypeVar

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.domain.errors import ConcurrencyConflict
from app.domain.invariants import InvariantReport, check_invariants, static_environment_digest
from app.domain.rules import Resolution
from app.domain.types import EngineTurnResult
from app.persistence.views import WorldView, freeze


T = TypeVar("T")
TransactionCallback = Callable[[Any | None], T]
TransactionRunner = Callable[[TransactionCallback[T]], T]

logger = logging.getLogger("many_lives.persistence")


class PersistenceError(RuntimeError):
    """Base error for missing or inconsistent canonical state."""


class StateNotFoundError(PersistenceError):
    """Raised when required campaign state does not exist."""


@dataclass(frozen=True)
class TurnClaim:
    state: Literal["NEW", "RECEIVED", "FINAL"]
    document: dict[str, Any]


class Repository:
    """Campaign-scoped persistence for state reads and atomic writes."""

    def __init__(
        self,
        db: Database,
        *,
        transaction_runner: TransactionRunner | None = None,
    ) -> None:
        self._db = db
        self._transaction_runner = transaction_runner or self._run_production_transaction

    def _run_production_transaction(self, callback: TransactionCallback[T]) -> T:
        with self._db.client.start_session() as session:
            return session.with_transaction(callback)

    def ensure_indexes(self) -> list[str]:
        """Create the repository's ordinary indexes before first durable write."""
        from app.persistence.indexes import create_btree_indexes

        return create_btree_indexes(self._db)

    @staticmethod
    def _session(session: Any | None) -> dict[str, Any]:
        return {} if session is None else {"session": session}

    # ---- reads ----

    def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        return self._db.campaigns.find_one({"_id": campaign_id})

    def list_campaigns(self) -> list[dict[str, Any]]:
        return list(self._db.campaigns.find().sort("updated_at", -1))

    def get_cells(
        self, campaign_id: str, cell_ids: list[str]
    ) -> list[dict[str, Any]]:
        if not cell_ids:
            return []
        return list(
            self._db.cells.find(
                {"campaign_id": campaign_id, "cell_id": {"$in": cell_ids}}
            )
        )

    def get_entity(self, campaign_id: str, entity_id: str) -> dict[str, Any] | None:
        return self._db.entities.find_one(
            {"campaign_id": campaign_id, "entity_id": entity_id}
        )

    def get_cell(self, campaign_id: str, cell_id: str) -> dict[str, Any] | None:
        return self._db.cells.find_one(
            {"campaign_id": campaign_id, "cell_id": cell_id}
        )

    def entities_in_cell(self, campaign_id: str, cell_id: str) -> list[dict[str, Any]]:
        return list(
            self._db.entities.find(
                {"campaign_id": campaign_id, "location.ref_id": cell_id}
            ).sort("entity_id", 1)
        )

    def unplaced_key_cells(self, campaign_id: str) -> list[str]:
        return sorted(
            {
                document["location"]["ref_id"]
                for document in self._db.entities.find(
                    {
                        "campaign_id": campaign_id,
                        "entity_type": "ITEM",
                        "item.subtype": "KEY",
                        "location.kind": "RESERVED",
                    }
                )
            }
        )

    def load_world_view(
        self,
        campaign_id: str,
        player_id: str,
        destination_cell_id: str | None = None,
    ) -> WorldView:
        campaign = self._db.campaigns.find_one({"_id": campaign_id})
        if campaign is None:
            raise StateNotFoundError(f"Campaign not found: {campaign_id}")
        player = self._db.entities.find_one(
            {"campaign_id": campaign_id, "entity_id": player_id, "entity_type": "PLAYER"}
        )
        if player is None:
            raise StateNotFoundError(f"Player not found: {player_id}")

        current_cell_id = player.get("location", {}).get("ref_id")
        current_cell = self._db.cells.find_one(
            {"campaign_id": campaign_id, "cell_id": current_cell_id}
        )
        if current_cell is None:
            raise StateNotFoundError(f"Current cell not found: {current_cell_id}")

        destination = None
        selected_cells = [current_cell_id]
        if destination_cell_id is not None:
            destination = self._db.cells.find_one(
                {"campaign_id": campaign_id, "cell_id": destination_cell_id}
            )
            if destination is None:
                raise StateNotFoundError(
                    f"Destination cell not found: {destination_cell_id}"
                )
            selected_cells.append(destination_cell_id)

        character_types = ["PLAYER", "NPC", "ENEMY", "BOSS"]
        characters = tuple(
            self._db.entities.find(
                {
                    "campaign_id": campaign_id,
                    "entity_type": {"$in": character_types},
                    "location.kind": "CELL",
                    "location.ref_id": {"$in": selected_cells},
                }
            ).sort("entity_id", 1)
        )
        items = tuple(
            self._db.entities.find(
                {
                    "campaign_id": campaign_id,
                    "entity_type": "ITEM",
                    "location.kind": "CELL",
                    "location.ref_id": {"$in": selected_cells},
                }
            ).sort("entity_id", 1)
        )
        feature_ids = [
            feature["feature_id"]
            for cell in (current_cell, destination)
            if cell is not None
            for feature in cell.get("features", [])
            if "feature_id" in feature
        ]
        container_items: tuple[dict[str, Any], ...] = ()
        if feature_ids:
            container_items = tuple(
                self._db.entities.find(
                    {
                        "campaign_id": campaign_id,
                        "entity_type": "ITEM",
                        "location.kind": "CONTAINER",
                        "location.ref_id": {"$in": feature_ids},
                    }
                ).sort("entity_id", 1)
            )

        owner_ids = [player_id, *(character["entity_id"] for character in characters)]
        owned_items = tuple(
            self._db.entities.find(
                {
                    "campaign_id": campaign_id,
                    "entity_type": "ITEM",
                    "location.kind": {"$in": ["INVENTORY", "EQUIPPED"]},
                    "location.ref_id": {"$in": owner_ids},
                }
            ).sort("entity_id", 1)
        )

        return WorldView(
            campaign=freeze(campaign),
            player=freeze(player),
            current_cell=freeze(current_cell),
            destination_cell=freeze(destination) if destination is not None else None,
            characters=tuple(freeze(document) for document in characters),
            items=tuple(freeze(document) for document in items),
            container_items=tuple(freeze(document) for document in container_items),
            config=freeze(campaign.get("config", {})),
            owned_items=tuple(freeze(document) for document in owned_items),
        )

    def history_stats(self, campaign_id: str) -> dict[str, int]:
        """Counts and stored size of one campaign's append-only history.

        Read-only and campaign-scoped (§5.10). `$bsonSize` needs MongoDB 4.4+;
        a deployment that rejects it reports zero bytes rather than failing,
        because this only feeds a display.
        """
        stats = {
            "events": self._db.events.count_documents({"campaign_id": campaign_id}),
            "memories": self._db.memories.count_documents({"campaign_id": campaign_id}),
            "turns": self._db.turns.count_documents({"campaign_id": campaign_id}),
            "stored_bytes": 0,
        }
        pipeline = [
            {"$match": {"campaign_id": campaign_id}},
            {"$group": {"_id": None, "bytes": {"$sum": {"$bsonSize": "$$ROOT"}}}},
        ]
        try:
            for collection in (self._db.events, self._db.memories):
                grouped = next(iter(collection.aggregate(pipeline)), None)
                if grouped is not None:
                    stats["stored_bytes"] += int(grouped["bytes"])
        except Exception:  # noqa: BLE001 - a size estimate is never worth a 500
            stats["stored_bytes"] = 0
        return stats

    # ---- writes ----

    def begin_turn(
        self,
        campaign_id: str,
        turn_id: str,
        player_id: str,
        input_text: str,
        *,
        kind: str = "ACTION",
        path: str = "FAST",
    ) -> TurnClaim:
        """Reserve a durable idempotency record or return the existing turn."""
        now = datetime.now(UTC)
        document = {
            "_id": f"{campaign_id}:{turn_id}",
            "campaign_id": campaign_id,
            "turn_id": turn_id,
            "kind": kind,
            "player_id": player_id,
            "input": input_text,
            "status": "RECEIVED",
            "path": path,
            "action_class": None,
            "proposal": None,
            "rejected_effects": [],
            "accepted_effect_types": [],
            "event_ids": [],
            "result": None,
            "model_calls": [],
            "context_manifest": None,
            "verification": None,
            "invariants": None,
            "created_at": now,
        }
        try:
            self._db.turns.insert_one(document)
            return TurnClaim("NEW", document)
        except DuplicateKeyError:
            existing = self._db.turns.find_one(
                {"campaign_id": campaign_id, "turn_id": turn_id}
            )
            if existing is None:
                raise PersistenceError("Duplicate turn exists outside campaign scope")
            state = "FINAL" if existing.get("status") in {
                "REJECTED", "COMMITTED", "NARRATED", "NARRATION_FAILED"
            } else "RECEIVED"
            return TurnClaim(state, existing)

    def get_turn(self, campaign_id: str, turn_id: str) -> dict[str, Any] | None:
        return self._db.turns.find_one(
            {"campaign_id": campaign_id, "turn_id": turn_id}
        )

    def check_campaign_invariants(self, campaign_id: str) -> InvariantReport:
        """Run the full campaign-scoped invariant sweep."""
        campaign = self.get_campaign(campaign_id)
        if campaign is None:
            raise StateNotFoundError(f"Campaign not found: {campaign_id}")
        return check_invariants(
            campaign,
            list(self._db.cells.find({"campaign_id": campaign_id})),
            list(self._db.entities.find({"campaign_id": campaign_id})),
            list(self._db.events.find({"campaign_id": campaign_id})),
            list(self._db.turns.find({"campaign_id": campaign_id})),
            expected_key_count=int(campaign.get("config", {}).get("key_reservations", 6)),
        )

    def update_turn(
        self, campaign_id: str, turn_id: str, fields: dict[str, Any]
    ) -> None:
        result = self._db.turns.update_one(
            {"campaign_id": campaign_id, "turn_id": turn_id}, {"$set": fields}
        )
        if result.matched_count != 1:
            raise StateNotFoundError(f"Turn not found: {turn_id}")

    def latest_turn(self, campaign_id: str) -> dict[str, Any] | None:
        return self._db.turns.find_one(
            {"campaign_id": campaign_id}, sort=[("created_at", -1)]
        )

    def reject_turn(
        self, campaign_id: str, turn_id: str, result: EngineTurnResult
    ) -> EngineTurnResult:
        stored = result.model_dump(mode="json")
        update = self._db.turns.update_one(
            {"campaign_id": campaign_id, "turn_id": turn_id, "status": "RECEIVED"},
            {"$set": {"status": "REJECTED", "result": stored,
                      "rejected_at": datetime.now(UTC)}},
        )
        if update.matched_count == 1:
            return result
        existing = self.get_turn(campaign_id, turn_id)
        if existing and existing.get("result"):
            return EngineTurnResult.model_validate(existing["result"])
        raise ConcurrencyConflict(f"Turn reservation lost: {turn_id}")

    def create_campaign(
        self,
        campaign: dict[str, Any],
        cells: list[dict[str, Any]],
        entities: list[dict[str, Any]],
        events: list[dict[str, Any]],
    ) -> None:
        campaign_id = campaign.get("_id")
        if not isinstance(campaign_id, str):
            raise ValueError("Campaign document requires a string _id")
        for collection_name, documents in (
            ("cells", cells),
            ("entities", entities),
            ("events", events),
        ):
            if not documents:
                raise ValueError(f"Initial {collection_name} cannot be empty")
            if any(document.get("campaign_id") != campaign_id for document in documents):
                raise ValueError(f"Every {collection_name} document must match campaign_id")

        def write(session: Any | None) -> None:
            options = self._session(session)
            self._db.campaigns.insert_one(campaign, **options)
            self._db.cells.insert_many(cells, **options)
            self._db.entities.insert_many(entities, **options)
            self._db.events.insert_many(events, **options)

        self._transaction_runner(write)

    def claim_room_generation(
        self,
        campaign_id: str,
        cell_id: str,
        *,
        stale_after_seconds: int,
    ) -> dict[str, Any]:
        """Atomically claim an ungenerated/stale room or return current state."""
        now = datetime.now(UTC)
        claimed = self._db.cells.find_one_and_update(
            {
                "campaign_id": campaign_id,
                "cell_id": cell_id,
                "generated": False,
                "generation_status": "UNGENERATED",
            },
            {"$set": {"generation_status": "GENERATING", "generation_started_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        if claimed is not None:
            return claimed
        current = self.get_cell(campaign_id, cell_id)
        if current is None:
            raise StateNotFoundError(f"Cell not found: {cell_id}")
        if current.get("generation_status") == "GENERATED":
            return current
        started = current.get("generation_started_at")
        if started is not None:
            # mongomock may deserialize aware datetimes as naive UTC.
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            cutoff = now.timestamp() - stale_after_seconds
            if started.timestamp() <= cutoff:
                reclaimed = self._db.cells.find_one_and_update(
                    {
                        "campaign_id": campaign_id,
                        "cell_id": cell_id,
                        "generated": False,
                        "generation_status": "GENERATING",
                        "generation_started_at": current["generation_started_at"],
                    },
                    {"$set": {"generation_started_at": now}},
                    return_document=ReturnDocument.AFTER,
                )
                if reclaimed is not None:
                    return reclaimed
        current["generation_status"] = "IN_PROGRESS"
        return current

    def commit_generated_room(
        self,
        *,
        campaign_id: str,
        cell_id: str,
        expected_version: int,
        room: dict[str, Any],
        features: list[dict[str, Any]],
        new_entities: list[dict[str, Any]],
        reserved_updates: list[dict[str, Any]],
        generation_source: str,
    ) -> list[str]:
        """Atomically materialize one claimed room and its generation event."""
        entity_ids = [entity["entity_id"] for entity in new_entities]
        entity_ids.extend(update["entity_id"] for update in reserved_updates)

        def write(session: Any | None) -> None:
            options = self._session(session)
            cell_result = self._db.cells.update_one(
                {
                    "campaign_id": campaign_id,
                    "cell_id": cell_id,
                    "generated": False,
                    "generation_status": "GENERATING",
                    "version": expected_version,
                },
                {
                    "$set": {
                        "room": room,
                        "features": features,
                        "generation_status": "GENERATED",
                        "generated": True,
                        "generation_source": generation_source,
                        "generation_started_at": None,
                        "static_environment_digest": static_environment_digest(
                            room["static_environment"]
                        ),
                    },
                    "$inc": {"version": 1},
                },
                **options,
            )
            if cell_result.matched_count != 1:
                raise PersistenceError(f"Room generation claim lost: {cell_id}")
            if new_entities:
                self._db.entities.insert_many(new_entities, **options)
            for update in reserved_updates:
                result = self._db.entities.update_one(
                    {
                        "campaign_id": campaign_id,
                        "entity_id": update["entity_id"],
                        "location.kind": "RESERVED",
                        "item.quest_critical": True,
                    },
                    {"$set": update["set"], "$inc": {"version": 1}},
                    **options,
                )
                if result.matched_count != 1:
                    raise PersistenceError(
                        f"Reserved item missing during generation: {update['entity_id']}"
                    )

            campaign = self._db.campaigns.find_one({"_id": campaign_id}, **options)
            if campaign is None:
                raise StateNotFoundError(f"Campaign not found: {campaign_id}")
            sequence = campaign["current_turn"]
            event_index = self._db.events.count_documents(
                {"campaign_id": campaign_id, "turn_sequence": sequence}, **options
            )
            event_id = f"evt_{sequence}_{event_index}"
            self._db.events.insert_one(
                {
                    "_id": f"{campaign_id}:{event_id}",
                    "campaign_id": campaign_id,
                    "schema_version": 1,
                    "event_id": event_id,
                    "turn_sequence": sequence,
                    "event_index": event_index,
                    "turn_id": f"room_generation:{cell_id}",
                    "type": "CELL_GENERATED",
                    "actor_id": "SYSTEM",
                    "entity_ids": entity_ids,
                    "cell_id": cell_id,
                    "payload": {
                        "archetype": room["archetype"],
                        "generation_source": generation_source,
                    },
                    "summary": f"Generated {room['name']} at {cell_id}.",
                    "memory_status": "NOT_REQUIRED",
                    "memory_attempts": 0,
                    "created_at": datetime.now(UTC),
                },
                **options,
            )

        self._transaction_runner(write)
        return entity_ids

    def commit_turn(
        self,
        campaign_id: str,
        player_id: str,
        resolution: Resolution,
        turn_result: EngineTurnResult | None = None,
        *,
        store_result: bool = True,
    ) -> EngineTurnResult | None:
        """Commit one turn, using the A4 CAS path for versioned resolutions."""
        if resolution.expected_turn is not None:
            return self._commit_versioned_turn(
                campaign_id, player_id, resolution, turn_result, store_result
            )
        if not resolution.accepted:
            return
        now = datetime.now(UTC)

        def write(session: Any | None) -> None:
            options = self._session(session)
            if resolution.events:
                event_documents = []
                for event in resolution.events:
                    if event.campaign_id != campaign_id:
                        raise PersistenceError("Resolution event campaign mismatch")
                    document = event.model_dump(mode="json")
                    document["_id"] = f"{campaign_id}:{event.event_id}"
                    document["created_at"] = now
                    event_documents.append(document)
                self._db.events.insert_many(event_documents, **options)

            player = self._db.entities.find_one(
                {"campaign_id": campaign_id, "entity_id": player_id}, **options
            )
            if player is None:
                raise StateNotFoundError(f"Player not found: {player_id}")

            player_update: dict[str, Any] = {"$inc": {"version": 1}}
            destination = resolution.state_updates.get("player_location")
            discovered = resolution.state_updates.get("discovered_cell")
            if destination is not None:
                player_update["$set"] = {
                    "location": destination,
                    "updated_turn": _resolution_turn_sequence(resolution),
                }
            if discovered is not None:
                player_update["$addToSet"] = {"player.discovered_cell_ids": discovered}
                known = player.get("player", {}).get("discovered_cell_ids", [])
                if discovered not in known:
                    player_update["$inc"]["player.new_cells_since_death"] = 1
            if "player_mp" in resolution.state_updates:
                player_update.setdefault("$set", {})["character.mp"] = resolution.state_updates[
                    "player_mp"
                ]

            for item in resolution.state_updates.get("item_documents", ()):
                entity_id = item["entity_id"]
                existing = self._db.entities.find_one(
                    {"campaign_id": campaign_id, "entity_id": entity_id}, **options
                )
                if existing is None:
                    document = dict(item)
                    document["_id"] = f"{campaign_id}:{entity_id}"
                    document["campaign_id"] = campaign_id
                    self._db.entities.insert_one(document, **options)
                else:
                    result = self._db.entities.update_one(
                        {"campaign_id": campaign_id, "entity_id": entity_id},
                        {"$set": {"location": item["location"], "item": item["item"]},
                         "$inc": {"version": 1}},
                        **options,
                    )
                    if result.matched_count != 1:
                        raise PersistenceError(f"Item update lost: {entity_id}")

            player_result = self._db.entities.update_one(
                {"campaign_id": campaign_id, "entity_id": player_id},
                player_update,
                **options,
            )
            if player_result.matched_count != 1:
                raise StateNotFoundError(f"Player not found during commit: {player_id}")

            if discovered is not None:
                cell_result = self._db.cells.update_one(
                    {"campaign_id": campaign_id, "cell_id": discovered},
                    {
                        "$addToSet": {"visited_by": player_id},
                        "$inc": {"version": 1},
                        "$set": {"last_updated_turn": _resolution_turn_sequence(resolution)},
                    },
                    **options,
                )
                if cell_result.matched_count != 1:
                    raise StateNotFoundError(f"Destination cell not found: {discovered}")

            campaign_result = self._db.campaigns.update_one(
                {"_id": campaign_id},
                {"$inc": {"current_turn": 1, "version": 1}, "$set": {"updated_at": now}},
                **options,
            )
            if campaign_result.matched_count != 1:
                raise StateNotFoundError(f"Campaign not found during commit: {campaign_id}")

        self._transaction_runner(write)
        return turn_result

    def _commit_versioned_turn(
        self,
        campaign_id: str,
        player_id: str,
        resolution: Resolution,
        turn_result: EngineTurnResult | None,
        store_result: bool,
    ) -> EngineTurnResult | None:
        if resolution.expected_turn is None:
            raise ValueError("Versioned resolution requires expected_turn")
        if not resolution.accepted:
            raise ValueError("Use reject_turn for rejected resolutions")
        sequence = resolution.expected_turn + 1
        turn_id = resolution.events[0].turn_id if resolution.events else resolution.turn_id
        if turn_result is not None:
            turn_id = turn_result.turn_id
        if not turn_id:
            raise ValueError("Versioned resolution requires a turn_id")
        if turn_result is None and store_result:
            turn_result = EngineTurnResult(
                turn_id=turn_id,
                turn_sequence=sequence,
                accepted=True,
                current_cell_id=resolution.current_cell_id or "",
                events=resolution.events,
                outcome_summary=resolution.outcome_summary,
            )
        existing = self.get_turn(campaign_id, turn_id)
        if existing and existing.get("status") in {
            "REJECTED", "COMMITTED", "NARRATED", "NARRATION_FAILED"
        }:
            if existing.get("result") is not None:
                return EngineTurnResult.model_validate(existing["result"])
            return None
        if existing is None:
            self.begin_turn(campaign_id, turn_id, player_id, "", path="FAST")

        now = datetime.now(UTC)
        campaign_mutations = [
            mutation for mutation in resolution.mutations
            if mutation.collection == "campaigns"
        ]
        if len(campaign_mutations) > 1:
            raise ValueError("A resolution may contain at most one campaign mutation")

        def write(session: Any | None) -> None:
            options = self._session(session)
            campaign_filter: dict[str, Any] = {
                "_id": campaign_id,
                "current_turn": resolution.expected_turn,
            }
            if resolution.expected_campaign_version is not None:
                campaign_filter["version"] = resolution.expected_campaign_version
            campaign_update: dict[str, Any] = {
                "$inc": {"current_turn": 1, "version": 1},
                "$set": {"updated_at": now},
            }
            if campaign_mutations:
                mutation = campaign_mutations[0]
                campaign_filter["version"] = mutation.expected_version
                campaign_update["$set"].update(mutation.set_fields)
                campaign_update["$inc"].update(mutation.inc_fields)
                if mutation.add_to_set_fields:
                    campaign_update["$addToSet"] = dict(mutation.add_to_set_fields)
            campaign_result = self._db.campaigns.update_one(
                campaign_filter, campaign_update, **options
            )
            if campaign_result.matched_count != 1:
                raise ConcurrencyConflict("Campaign turn/version changed before commit")

            for mutation in resolution.mutations:
                if mutation.collection == "campaigns":
                    continue
                collection = self._db[mutation.collection]
                local_key = "entity_id" if mutation.collection == "entities" else "cell_id"
                update: dict[str, Any] = {
                    "$inc": {"version": 1, **dict(mutation.inc_fields)}
                }
                if mutation.set_fields:
                    update["$set"] = dict(mutation.set_fields)
                if mutation.add_to_set_fields:
                    update["$addToSet"] = dict(mutation.add_to_set_fields)
                result = collection.update_one(
                    {"campaign_id": campaign_id, local_key: mutation.document_id,
                     "version": mutation.expected_version},
                    update,
                    **options,
                )
                if result.matched_count != 1:
                    raise ConcurrencyConflict(
                        f"{mutation.collection} version changed: {mutation.document_id}"
                    )

            for insertion in resolution.inserts:
                document = dict(insertion.document)
                entity_id = document["entity_id"]
                document["_id"] = f"{campaign_id}:{entity_id}"
                document["campaign_id"] = campaign_id
                document.setdefault("schema_version", 1)
                document.setdefault("version", 0)
                document.setdefault("created_turn", sequence)
                document.setdefault("updated_turn", sequence)
                self._db[insertion.collection].insert_one(document, **options)

            if resolution.events:
                documents = []
                for event in resolution.events:
                    if event.campaign_id != campaign_id or event.turn_id != turn_id:
                        raise PersistenceError("Resolution event scope mismatch")
                    document = event.model_dump(mode="json")
                    document["_id"] = f"{campaign_id}:{event.event_id}"
                    document["created_at"] = now
                    documents.append(document)
                self._db.events.insert_many(documents, **options)

            committed_fields: dict[str, Any] = {
                "status": "COMMITTED",
                "turn_sequence": sequence,
                "event_ids": [event.event_id for event in resolution.events],
                "accepted_effect_types": [event.type.value for event in resolution.events],
                "touched_entity_ids": resolution.touched_entity_ids,
                "touched_cell_ids": resolution.touched_cell_ids,
                "committed_at": now,
            }
            if turn_result is not None:
                committed_fields["result"] = turn_result.model_dump(mode="json")
            turn_update = self._db.turns.update_one(
                {"campaign_id": campaign_id, "turn_id": turn_id,
                 "status": "RECEIVED"},
                {"$set": committed_fields},
                **options,
            )
            if turn_update.matched_count != 1:
                raise ConcurrencyConflict(f"Turn reservation lost: {turn_id}")

        try:
            self._transaction_runner(write)
        except DuplicateKeyError:
            replay = self.get_turn(campaign_id, turn_id)
            if replay and replay.get("result"):
                return EngineTurnResult.model_validate(replay["result"])
            raise
        # Post-commit by design: invariant failures describe committed state
        # and never roll it back (TDD §9.10).
        try:
            report = self.check_campaign_invariants(campaign_id)
            invariant_document = report.to_document()
        except Exception as exc:
            # The canonical transaction has committed. Diagnostics must never
            # turn that success into an apparent failed request/retry.
            logger.exception(
                "post-commit invariant sweep failed",
                extra={"campaign_id": campaign_id, "turn_id": turn_id},
            )
            invariant_document = {
                "checked": 0,
                "failures": [{"invariant": "CHECKER", "message": str(exc)}],
            }
        try:
            self._db.turns.update_one(
                {"campaign_id": campaign_id, "turn_id": turn_id},
                {"$set": {"invariants": invariant_document}},
            )
        except Exception:
            logger.exception(
                "post-commit invariant result could not be stored",
                extra={"campaign_id": campaign_id, "turn_id": turn_id},
            )
        return turn_result


def _resolution_turn_sequence(resolution: Resolution) -> int:
    if resolution.events:
        return resolution.events[0].turn_sequence
    return 0
