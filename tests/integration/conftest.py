"""Integration-test harness (TDD §20.2).

Two execution modes, chosen automatically:

* **Stub mode** (default, no credentials): the in-memory seam from
  `app.services.stubs`. Everything that does not require durability runs here,
  so the suite is useful before A's persistence lands.
* **Atlas mode** (`MONGODB_URI` set): the same tests run against the
  `dungeon_test` database. Anything requiring a real restart is skipped —
  loudly, with a reason — while the installed engine reports `DURABLE = False`.

The database name is forced to `dungeon_test` (§19) so a stray `.env` pointing
at the demo database can never be dropped by a test run.
"""

from __future__ import annotations

import os

import pytest

TEST_DB_NAME = "dungeon_test"

# Collections a test run is allowed to clear. Anything else in the test
# database is left alone.
_OWNED_COLLECTIONS = (
    "campaigns",
    "cells",
    "entities",
    "events",
    "memories",
    "turns",
    "quests",
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "durable: requires an engine whose state survives a restart"
    )


@pytest.fixture(scope="session")
def mongo_uri() -> str | None:
    """The Atlas test URI, or None when integration tests should use stubs."""
    uri = os.getenv("MONGODB_URI", "").strip()
    # A placeholder in .env.example must not be mistaken for a real cluster.
    if not uri or uri.startswith("<") or "example" in uri:
        return None
    return uri


@pytest.fixture(scope="session")
def mongo_db(mongo_uri: str | None):
    """A handle to `dungeon_test`, or a clean skip when Atlas is unavailable.

    Never yields a handle to any database other than `dungeon_test`.
    """
    if mongo_uri is None:
        pytest.skip("MONGODB_URI is not set; running against in-memory stubs")

    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except PyMongoError as exc:  # unreachable cluster is a skip, not a failure
        client.close()
        pytest.skip(f"Atlas unreachable: {type(exc).__name__}")

    database = client[TEST_DB_NAME]
    assert database.name == TEST_DB_NAME, "tests must never touch the demo database"
    try:
        yield database
    finally:
        client.close()


@pytest.fixture
def clean_db(mongo_db):
    """Empty the owned collections before and after a test."""

    def _clear() -> None:
        for name in _OWNED_COLLECTIONS:
            mongo_db[name].delete_many({})

    _clear()
    yield mongo_db
    _clear()


@pytest.fixture
def engine_is_durable() -> bool:
    """Whether the installed engine keeps state across a process restart."""
    import importlib

    # Resolved dynamically: a restart test may have reloaded the module.
    stubs = importlib.import_module("app.services.stubs")
    return bool(getattr(stubs.get_engine(), "DURABLE", False))


@pytest.fixture
def require_durable(engine_is_durable: bool) -> None:
    """Skip a restart test while the seam is backed by the in-memory stub."""
    if not engine_is_durable:
        pytest.skip(
            "installed engine is not durable (in-memory stub); "
            "needs Developer A's persistence behind get_engine()"
        )
