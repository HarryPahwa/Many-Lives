"""Offline tests for the MongoDB index catalog and vector definition."""

from unittest.mock import MagicMock

import mongomock
import pytest

from app.persistence.indexes import (
    BTREE_INDEXES,
    MEMORY_VECTOR_FILTER_PATHS,
    MEMORY_VECTOR_INDEX_NAME,
    create_btree_indexes,
    ensure_memory_vector_index,
    memory_vector_definition,
)


def test_btree_catalog_is_created_with_exact_keys_and_options() -> None:
    db = mongomock.MongoClient().dungeon_test

    names = create_btree_indexes(db)

    assert names == [spec.name for spec in BTREE_INDEXES]
    assert len(names) == 22
    for spec in BTREE_INDEXES:
        actual = db[spec.collection].index_information()[spec.name]
        assert actual["key"] == list(spec.keys)
        assert actual.get("unique", False) is spec.unique


def test_btree_creation_is_idempotent() -> None:
    db = mongomock.MongoClient().dungeon_test
    first = create_btree_indexes(db)
    second = create_btree_indexes(db)
    assert second == first


def test_campaign_scoped_unique_indexes_include_campaign_id() -> None:
    scoped_collections = {"cells", "entities", "events", "turns"}
    unique_specs = [spec for spec in BTREE_INDEXES if spec.unique]

    for spec in unique_specs:
        if spec.collection in scoped_collections:
            assert spec.keys[0] == ("campaign_id", 1)


def test_no_source_event_ids_index_exists() -> None:
    assert all(
        key != "source_event_ids"
        for spec in BTREE_INDEXES
        for key, _direction in spec.keys
    )


def test_vector_definition_has_exact_vector_and_filter_fields() -> None:
    definition = memory_vector_definition(1536)
    vector, *filters = definition["fields"]

    assert vector == {
        "type": "vector",
        "path": "embedding",
        "numDimensions": 1536,
        "similarity": "cosine",
    }
    assert filters == [
        {"type": "filter", "path": path} for path in MEMORY_VECTOR_FILTER_PATHS
    ]


@pytest.mark.parametrize("dimensions", [0, -1, True, 1.5, "1536"])
def test_vector_definition_rejects_invalid_dimensions(dimensions: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        memory_vector_definition(dimensions)  # type: ignore[arg-type]


class FakeSearchCollection:
    def __init__(self, states: list[dict | None]):
        self.states = iter(states)
        self.created_models: list[object] = []

    def list_search_indexes(self, *, name: str):
        assert name == MEMORY_VECTOR_INDEX_NAME
        state = next(self.states)
        return iter([] if state is None else [state])

    def create_search_index(self, *, model: object) -> None:
        self.created_models.append(model)


def test_vector_index_is_created_then_polled_until_queryable() -> None:
    collection = FakeSearchCollection([None, {"queryable": True}])
    db = MagicMock()
    db.memories = collection

    ensure_memory_vector_index(db, 768, timeout_s=0, poll_interval_s=0)

    assert len(collection.created_models) == 1
    model_document = collection.created_models[0].document  # type: ignore[attr-defined]
    assert model_document["name"] == MEMORY_VECTOR_INDEX_NAME
    assert model_document["type"] == "vectorSearch"
    assert model_document["definition"] == memory_vector_definition(768)


def test_ready_vector_index_is_not_recreated() -> None:
    definition = memory_vector_definition(768)
    collection = FakeSearchCollection(
        [{"queryable": True, "latestDefinition": definition}, {"queryable": True}]
    )
    db = MagicMock()
    db.memories = collection

    ensure_memory_vector_index(db, 768, timeout_s=0, poll_interval_s=0)
    assert collection.created_models == []


def test_mismatched_existing_vector_dimension_is_rejected() -> None:
    collection = FakeSearchCollection(
        [{"queryable": True, "latestDefinition": memory_vector_definition(1536)}]
    )
    db = MagicMock()
    db.memories = collection

    with pytest.raises(RuntimeError, match="does not match"):
        ensure_memory_vector_index(db, 768, timeout_s=0, poll_interval_s=0)


def test_vector_polling_has_bounded_timeout() -> None:
    collection = FakeSearchCollection([None, {"queryable": False}])
    db = MagicMock()
    db.memories = collection

    with pytest.raises(TimeoutError, match="queryable"):
        ensure_memory_vector_index(db, 768, timeout_s=0, poll_interval_s=0)
