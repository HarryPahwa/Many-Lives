from app.persistence.sqlite import get_sqlite_connection

from app.domain.rules import resolve_world_action
from app.domain.types import ActionIntent, ActionType
from app.persistence.indexes import create_btree_indexes
from app.persistence.repositories import Repository
from app.services.campaign_service import create_campaign


CAMPAIGN_ID = "cmp_a5sweep00001"


class ImmediateTransactions:
    def __call__(self, callback):
        return callback(None)


def test_fifty_committed_turns_have_zero_invariant_failures():
    db = get_sqlite_connection(":memory:")
    create_btree_indexes(db)
    repository = Repository(db, transaction_runner=ImmediateTransactions())
    created = create_campaign(repository, "Ada", seed=505, campaign_id=CAMPAIGN_ID)
    for index in range(50):
        turn_id = f"sweep-{index}"
        repository.begin_turn(CAMPAIGN_ID, turn_id, created.player_id, "look")
        resolution = resolve_world_action(
            ActionIntent(action_type=ActionType.LOOK, actor_id=created.player_id),
            repository.load_world_view(CAMPAIGN_ID, created.player_id),
            turn_id=turn_id,
        )
        repository.commit_turn(CAMPAIGN_ID, created.player_id, resolution)
        record = repository.get_turn(CAMPAIGN_ID, turn_id)
        assert record["invariants"] == {"checked": 15, "failures": []}
    assert repository.check_campaign_invariants(CAMPAIGN_ID).passed
    assert db.campaigns.find_one({"_id": CAMPAIGN_ID})["current_turn"] == 50


def test_post_commit_checker_failure_does_not_report_committed_turn_as_failed(monkeypatch):
    db = get_sqlite_connection(":memory:")
    create_btree_indexes(db)
    repository = Repository(db, transaction_runner=ImmediateTransactions())
    created = create_campaign(repository, "Ada", seed=506, campaign_id=CAMPAIGN_ID)
    turn_id = "checker-failure"
    repository.begin_turn(CAMPAIGN_ID, turn_id, created.player_id, "look")
    resolution = resolve_world_action(
        ActionIntent(action_type=ActionType.LOOK, actor_id=created.player_id),
        repository.load_world_view(CAMPAIGN_ID, created.player_id),
        turn_id=turn_id,
    )
    monkeypatch.setattr(
        repository,
        "check_campaign_invariants",
        lambda _campaign_id: (_ for _ in ()).throw(RuntimeError("checker unavailable")),
    )

    result = repository.commit_turn(CAMPAIGN_ID, created.player_id, resolution)

    assert result is not None and result.accepted
    assert db.campaigns.find_one({"_id": CAMPAIGN_ID})["current_turn"] == 1
    record = repository.get_turn(CAMPAIGN_ID, turn_id)
    assert record["status"] == "COMMITTED"
    assert record["invariants"]["failures"][0]["invariant"] == "CHECKER"
