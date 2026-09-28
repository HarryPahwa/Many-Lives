"""Offline tests for the SQLite index catalog and initialized schema."""

from app.persistence.indexes import BTREE_INDEXES, create_btree_indexes
from app.persistence.sqlite import get_sqlite_connection


def test_btree_catalog_is_stable_and_idempotent() -> None:
    db = get_sqlite_connection(":memory:")
    expected = [spec.name for spec in BTREE_INDEXES]
    assert create_btree_indexes(db) == expected
    assert create_btree_indexes(db) == expected
    assert len(expected) == 22


def test_core_tables_and_physical_indexes_exist() -> None:
    db = get_sqlite_connection(":memory:")
    tables = {
        row[0]
        for row in db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"campaigns", "cells", "entities", "events", "turns", "memories"} <= tables
    indexes = {
        row[0]
        for row in db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    assert {
        "cells_campaign_cell",
        "entities_campaign_entity",
        "entities_location",
        "events_sequence",
        "memories_campaign_memory",
    } <= indexes


def test_campaign_scoped_unique_catalog_entries_start_with_campaign_id() -> None:
    scoped = {"cells", "entities", "events", "turns"}
    for spec in BTREE_INDEXES:
        if spec.unique and spec.collection in scoped:
            assert spec.keys[0] == ("campaign_id", 1)


def test_no_source_event_ids_index_exists() -> None:
    assert all(
        key != "source_event_ids"
        for spec in BTREE_INDEXES
        for key, _direction in spec.keys
    )
