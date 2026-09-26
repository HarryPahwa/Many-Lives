"""Mongo client and database lifecycle (TDD §6.3, §9, §19).

Importing this module never opens a connection. The first caller gets a shared
synchronous client and a database configured with majority write concern.
"""

from __future__ import annotations

import os

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.write_concern import WriteConcern


class MongoConfigurationError(RuntimeError):
    """Raised when required MongoDB environment configuration is absent."""


_client: MongoClient | None = None
_database: Database | None = None


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise MongoConfigurationError(f"Required environment variable {name} is not set")
    return value


def get_mongo_client() -> MongoClient:
    """Return the process-wide client, creating it lazily."""
    global _client
    if _client is None:
        uri = _required_environment("MONGODB_URI")
        _client = MongoClient(uri)
    return _client


def get_database() -> Database:
    """Return the configured database with majority write concern."""
    global _database
    if _database is None:
        database_name = _required_environment("MONGODB_DB")
        _database = get_mongo_client()[database_name].with_options(
            write_concern=WriteConcern(w="majority")
        )
    return _database


def close_mongo_client() -> None:
    """Close and clear cached handles; safe to call more than once."""
    global _client, _database
    client = _client
    _client = None
    _database = None
    if client is not None:
        client.close()
