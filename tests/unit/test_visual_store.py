"""Room Visuals §14.1 item 4 — store behaviour across every backend.

The claim is the interesting part: it is what stops two clients paying for the
same image twice, and what lets a server killed mid-generation recover.
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timedelta, timezone

import pytest

from app.services.visual_store import (
    STATUS_GENERATING,
    STATUS_READY,
    FileVisualStore,
    MemoryVisualStore,
    VisualAsset,
    VisualRecord,
    is_valid_asset_id,
    new_asset_id,
    safe_segment,
)

CAMPAIGN = "cmp_test0001"
CELL = "cell_1_1"


@pytest.fixture(params=["memory", "file"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryVisualStore()
    return FileVisualStore(str(tmp_path / "visuals"))


def _asset(asset_id: str, revision: int, data: bytes = b"\xff\xd8\xff\xdbJPEG") -> VisualAsset:
    return VisualAsset(
        asset_id=asset_id,
        campaign_id=CAMPAIGN,
        cell_id=CELL,
        revision=revision,
        signature="sha256:x",
        media_type="image/jpeg",
        data=data,
        size=len(data),
        model="test/model",
        kind="GENERATE",
    )


# ---------------------------------------------------------------------------
# Claim (VIS-05)
# ---------------------------------------------------------------------------


def test_a_fresh_cell_can_be_claimed(store):
    record = store.claim(CAMPAIGN, CELL)
    assert record is not None
    assert record.status == STATUS_GENERATING
    assert record.generation_started_at


def test_a_second_claim_is_refused_while_one_is_live(store):
    assert store.claim(CAMPAIGN, CELL) is not None
    assert store.claim(CAMPAIGN, CELL) is None, "two generations would both be billed"


def test_a_stale_claim_can_be_reclaimed(store):
    """A server killed mid-generation must not lock the cell forever (§8.4)."""
    claimed = store.claim(CAMPAIGN, CELL)
    assert claimed is not None

    stale = store.get(CAMPAIGN, CELL)
    stale.generation_started_at = (
        datetime.now(timezone.utc) - timedelta(seconds=999)
    ).isoformat()
    store.put(stale)

    assert store.claim(CAMPAIGN, CELL) is not None


def test_a_claim_older_than_the_window_but_not_stale_is_still_refused(store):
    claimed = store.claim(CAMPAIGN, CELL)
    assert claimed is not None
    recent = store.get(CAMPAIGN, CELL)
    recent.generation_started_at = (
        datetime.now(timezone.utc) - timedelta(seconds=5)
    ).isoformat()
    store.put(recent)
    assert store.claim(CAMPAIGN, CELL) is None


def test_a_corrupt_timestamp_does_not_wedge_the_cell(store):
    """Better to allow a second render than to lock a room out forever."""
    store.claim(CAMPAIGN, CELL)
    broken = store.get(CAMPAIGN, CELL)
    broken.generation_started_at = "not-a-timestamp"
    store.put(broken)
    assert store.claim(CAMPAIGN, CELL) is not None


def test_a_finished_record_can_be_claimed_again(store):
    store.claim(CAMPAIGN, CELL)
    done = store.get(CAMPAIGN, CELL)
    done.status = STATUS_READY
    store.put(done)
    assert store.claim(CAMPAIGN, CELL) is not None


def test_concurrent_claims_yield_exactly_one_winner(store):
    """Threads racing on the same cell must not both proceed."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.claim(CAMPAIGN, CELL), range(8)))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1, f"{len(winners)} claims succeeded; exactly one may"


def test_claims_on_different_cells_do_not_block_each_other(store):
    assert store.claim(CAMPAIGN, "cell_1_1") is not None
    assert store.claim(CAMPAIGN, "cell_2_2") is not None


def test_claims_are_scoped_by_campaign(store):
    assert store.claim("cmp_a0000001", CELL) is not None
    assert store.claim("cmp_b0000002", CELL) is not None


# ---------------------------------------------------------------------------
# Assets and scoping (VIS-06)
# ---------------------------------------------------------------------------


def test_an_asset_round_trips(store):
    asset_id = new_asset_id()
    store.put_asset(_asset(asset_id, 1))
    found = store.get_asset(CAMPAIGN, asset_id)
    assert found is not None
    assert found.data == b"\xff\xd8\xff\xdbJPEG"
    assert found.media_type == "image/jpeg"


def test_an_asset_id_from_another_campaign_is_a_miss(store):
    asset_id = new_asset_id()
    store.put_asset(_asset(asset_id, 1))
    assert store.get_asset("cmp_someoneelse", asset_id) is None


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../../etc/passwd",
        "va_../../secret",
        "va_zzzz",
        "va_" + "0" * 31,
        "va_" + "0" * 33,
        "",
        "asset_1",
        "va_ABCDEF01234567890123456789012345",  # uppercase hex not allowed
    ],
)
def test_malformed_asset_ids_are_rejected(store, bad_id):
    assert store.get_asset(CAMPAIGN, bad_id) is None


def test_asset_id_shape_is_enforced_centrally():
    assert is_valid_asset_id(new_asset_id())
    assert not is_valid_asset_id("va_../x")
    assert not safe_segment("../etc")
    assert safe_segment("cmp_stub00000001")


def test_the_file_store_refuses_to_build_a_traversing_path(tmp_path):
    store = FileVisualStore(str(tmp_path / "v"))
    with pytest.raises(ValueError):
        store._asset_path("../../escape", new_asset_id())
    with pytest.raises(ValueError):
        store._asset_path(CAMPAIGN, "../../escape")


# ---------------------------------------------------------------------------
# Pruning (§8.3)
# ---------------------------------------------------------------------------


def test_pruning_keeps_the_newest_and_never_drops_current_or_base(store):
    ids = [new_asset_id() for _ in range(6)]
    for revision, asset_id in enumerate(ids, start=1):
        store.put_asset(_asset(asset_id, revision))

    record = VisualRecord(
        campaign_id=CAMPAIGN,
        cell_id=CELL,
        status=STATUS_READY,
        revision=6,
        current_asset_id=ids[-1],
        base_asset_id=ids[0],  # the oldest: must survive the cull
    )
    store.put(record)

    store.prune(CAMPAIGN, CELL, keep=2)

    assert store.get_asset(CAMPAIGN, ids[-1]) is not None, "current was deleted"
    assert store.get_asset(CAMPAIGN, ids[0]) is not None, "base was deleted"


def test_pruning_is_safe_on_an_unknown_cell(store):
    assert store.prune(CAMPAIGN, "cell_9_9", keep=3) == 0


# ---------------------------------------------------------------------------
# File store durability
# ---------------------------------------------------------------------------


def test_the_file_store_survives_a_new_instance(tmp_path):
    root = str(tmp_path / "visuals")
    first = FileVisualStore(root)
    asset_id = new_asset_id()
    first.put_asset(_asset(asset_id, 1))
    record = VisualRecord(
        campaign_id=CAMPAIGN, cell_id=CELL, status=STATUS_READY, revision=1,
        current_asset_id=asset_id,
    )
    first.put(record)

    # A restart: everything in memory is gone.
    second = FileVisualStore(root)
    assert second.get(CAMPAIGN, CELL).current_asset_id == asset_id
    assert second.get_asset(CAMPAIGN, asset_id).data == b"\xff\xd8\xff\xdbJPEG"


def test_a_corrupt_index_does_not_raise(tmp_path):
    root = tmp_path / "visuals"
    root.mkdir(parents=True)
    (root / "index.json").write_text("{not json", encoding="utf-8")
    store = FileVisualStore(str(root))
    assert store.get(CAMPAIGN, CELL) is None
    assert store.claim(CAMPAIGN, CELL) is not None


def test_the_file_store_leaves_no_temp_files(tmp_path):
    root = tmp_path / "visuals"
    store = FileVisualStore(str(root))
    store.put_asset(_asset(new_asset_id(), 1))
    store.claim(CAMPAIGN, CELL)
    assert list(root.rglob("*.tmp")) == []
