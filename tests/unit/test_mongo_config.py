"""Offline tests for Mongo configuration and handle lifecycle."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.persistence import mongo


@pytest.fixture(autouse=True)
def reset_mongo(monkeypatch: pytest.MonkeyPatch):
    mongo.close_mongo_client()
    monkeypatch.setattr(
        mongo,
        "get_settings",
        lambda: SimpleNamespace(mongodb_uri="", mongodb_db=""),
    )
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("MONGODB_DB", raising=False)
    yield
    mongo.close_mongo_client()


def test_import_has_no_client_side_effect() -> None:
    assert mongo._client is None
    assert mongo._database is None


@pytest.mark.parametrize("missing", ["MONGODB_URI", "MONGODB_DB"])
def test_missing_configuration_fails_without_exposing_values(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb+srv://secret-user:secret-password@example")
    monkeypatch.setenv("MONGODB_DB", "dungeon")
    monkeypatch.delenv(missing)

    with pytest.raises(mongo.MongoConfigurationError) as error:
        mongo.get_database()

    assert missing in str(error.value)
    assert "secret-password" not in str(error.value)


def test_client_and_database_are_cached_and_database_uses_majority_write_concern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://example.invalid")
    monkeypatch.setenv("MONGODB_DB", "dungeon_test")
    database = MagicMock()
    majority_database = MagicMock()
    database.with_options.return_value = majority_database
    client = MagicMock()
    client.__getitem__.return_value = database
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr(mongo, "MongoClient", constructor)

    assert mongo.get_mongo_client() is client
    assert mongo.get_mongo_client() is client
    assert mongo.get_database() is majority_database
    assert mongo.get_database() is majority_database

    constructor.assert_called_once_with("mongodb://example.invalid")
    client.__getitem__.assert_called_once_with("dungeon_test")
    write_concern = database.with_options.call_args.kwargs["write_concern"]
    assert write_concern.document == {"w": "majority"}


def test_close_is_idempotent_and_allows_reconfiguration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://first.invalid")
    first = MagicMock()
    second = MagicMock()
    constructor = MagicMock(side_effect=[first, second])
    monkeypatch.setattr(mongo, "MongoClient", constructor)

    assert mongo.get_mongo_client() is first
    mongo.close_mongo_client()
    mongo.close_mongo_client()
    monkeypatch.setenv("MONGODB_URI", "mongodb://second.invalid")
    assert mongo.get_mongo_client() is second

    first.close.assert_called_once_with()
    assert constructor.call_args_list[0].args == ("mongodb://first.invalid",)
    assert constructor.call_args_list[1].args == ("mongodb://second.invalid",)
