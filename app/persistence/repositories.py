"""Repositories (TDD §9.9).

Campaign-scoped reads and the commit_turn write path. Translates a
Resolution's state updates and events into MongoDB operations; never decides
game outcomes (that is the rules engine's job).

The tracer bullet keeps commit_turn a simple regrouped write. Atomicity via
multi-document transactions + optimistic version checks is hardened later
(TDD A4/A5); on the Atlas shared Free tier multi-document transactions are
unavailable, so the eventual seam is a version-check / CAS, not a Mongo txn.
"""

from typing import Any

from pymongo.database import Database

from app.domain.rules import Resolution


class Repository:
    """Campaign-scoped persistence for state reads and turn commits."""

    def __init__(self, db: Database):
        self._db = db

    # ---- reads ----

    def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        return self._db.campaigns.find_one({"_id": campaign_id})

    def get_entity(self, campaign_id: str, entity_id: str) -> dict[str, Any] | None:
        return self._db.entities.find_one({"_id": f"{campaign_id}:{entity_id}"})

    # ---- writes ----

    def commit_turn(
        self,
        campaign_id: str,
        player_id: str,
        resolution: Resolution,
    ) -> None:
        """Apply a resolved turn: mutate state, append events, bump the counter.

        Idempotency (skip if turn_id already committed) and optimistic
        version checks are added later; this is the minimal correct path.
        """
        for event in resolution.events:
            event_doc = event.model_dump(mode="json")
            event_doc["_id"] = f"{campaign_id}:{event.event_id}"
            self._db.events.insert_one(event_doc)

        if "player_location" in resolution.state_updates:
            self._db.entities.update_one(
                {"_id": f"{campaign_id}:{player_id}"},
                {"$set": {"location": resolution.state_updates["player_location"]}},
            )

        self._db.campaigns.update_one(
            {"_id": campaign_id},
            {"$inc": {"current_turn": 1}},
        )