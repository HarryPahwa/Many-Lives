from app.domain.types import CellSnapshot, Claim, ClaimAttribute, NarrationResult, SnapshotEntity
from app.harness.narration_verifier import verify


def snapshot() -> CellSnapshot:
    return CellSnapshot(
        cell_id="cell_1_1",
        name="Crypt",
        characters=[
            SnapshotEntity(
                entity_id="npc_1", name="Mara", kind="CHARACTER", status="DEAD", disposition="HOSTILE"
            )
        ],
        items=[SnapshotEntity(entity_id="item_1", name="Key", kind="ITEM", location="player_inventory")],
    )


def test_verifier_counts_mismatches_unknowns_and_absent_names():
    result = NarrationResult(
        prose="The unseen Goblin laughs.",
        claims=[
            Claim(entity_id="npc_1", attribute=ClaimAttribute.STATUS, value="ALIVE"),
            Claim(entity_id="invented", attribute=ClaimAttribute.PRESENT, value=True),
        ],
    )
    verification = verify(result, snapshot(), {"npc_1": "Mara", "enemy_1": "Goblin"})
    assert verification.claims_checked == 1
    assert verification.contradictions == 1
    assert verification.unknown_entities == 1
    assert verification.absent_entity_mentions == 1


def test_verifier_accepts_carried_item_presence_and_location():
    result = NarrationResult(
        prose="You carry the Key.",
        claims=[
            Claim(entity_id="item_1", attribute=ClaimAttribute.PRESENT, value=True),
            Claim(entity_id="item_1", attribute=ClaimAttribute.LOCATION, value="player_inventory"),
        ],
    )
    verification = verify(result, snapshot(), {"item_1": "Key"})
    assert verification.contradictions == 0
