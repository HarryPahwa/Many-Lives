from app.domain.types import CellSnapshot, Claim, ClaimAttribute, NarrationResult, PlayerSummary, Role, SnapshotEntity
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


def test_fallback_claims_every_visible_entity_present():
    result = fallback_narration([], snapshot())
    assert {claim.entity_id for claim in result.claims} == {"feat_1", "npc_1", "item_1"}
