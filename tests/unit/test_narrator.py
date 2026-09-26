from app.domain.types import (
    CellSnapshot,
    Claim,
    ClaimAttribute,
    Event,
    EventType,
    NarrationResult,
    PlayerSummary,
    Role,
    SnapshotEntity,
)
from app.harness.model_client import FakeModelClient
from app.harness.narrator import fallback_narration, narrate


def snapshot() -> CellSnapshot:
    return CellSnapshot(
        cell_id="cell_1_1",
        name="Moss Crypt",
        features=[SnapshotEntity(entity_id="feat_1", name="chair", kind="FEATURE")],
        characters=[SnapshotEntity(entity_id="npc_1", name="Mara", kind="CHARACTER", status="ALIVE")],
        items=[SnapshotEntity(entity_id="item_1", name="key", kind="ITEM")],
    )


def player() -> PlayerSummary:
    return PlayerSummary(player_id="player_1", hp=10, max_hp=20, mp=4, max_mp=6, level=1)


def test_narrator_uses_model_contract_and_returns_call_record():
    fixture = NarrationResult(
        prose="You find Mara beside the chair.",
        claims=[Claim(entity_id="npc_1", attribute=ClaimAttribute.PRESENT, value=True)],
    )
    client = FakeModelClient({(Role.NARRATOR, "default"): fixture})

    result, call = narrate(
        events=None,
        rejection_reason=None,
        snapshot=snapshot(),
        player_summary=player(),
        social=None,
        context_text="current state wins",
        client=client,
    )

    assert result == fixture
    assert call.role is Role.NARRATOR
    assert client.calls[0]["temperature"] == 0.7
    assert "current state wins" in client.calls[0]["user"]


def test_narration_replaces_ids_the_snapshot_no_longer_contains():
    prose = "You are slain by enemy_5_3_1 for 4 damage. You drop item_5_4_1."
    fixture = NarrationResult(prose=prose, claims=[])
    client = FakeModelClient({(Role.NARRATOR, "default"): fixture})
    death = Event(
        campaign_id="c", event_id="evt_1", turn_sequence=1, event_index=1,
        turn_id="t", type=EventType.PLAYER_DIED, actor_id="player", cell_id="cell_5_3",
        summary="You are slain by tunnel goblin.",
        payload={"killer_ids": ["enemy_5_3_1"], "killer_names": ["tunnel goblin"]},
    )
    dropped = Event(
        campaign_id="c", event_id="evt_2", turn_sequence=1, event_index=2,
        turn_id="t", type=EventType.ITEM_DROPPED, actor_id="player", cell_id="cell_5_3",
        summary="You drop brass key.",
        payload={"item_id": "item_5_4_1", "item_name": "brass key"},
    )

    result, _call = narrate(
        events=[death, dropped],
        rejection_reason=None,
        snapshot=snapshot(),
        player_summary=player(),
        social=None,
        context_text="",
        client=client,
    )

    assert result.prose == "You are slain by tunnel goblin for 4 damage. You drop brass key."


def test_fallback_claims_every_visible_entity_present():
    result = fallback_narration([], snapshot())
    assert {claim.entity_id for claim in result.claims} == {"feat_1", "npc_1", "item_1"}
