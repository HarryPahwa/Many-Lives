"""Offline tests for SQLite configuration and lifecycle."""

from pathlib import Path

from app.persistence import sqlite


def test_memory_database_enables_foreign_keys_and_json() -> None:
    database = sqlite.get_sqlite_connection(":memory:")
    assert database.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert database.connection.execute("SELECT json_valid('{}')").fetchone()[0] == 1
    database.close()


def test_file_database_uses_wal_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "world.db"
    database = sqlite.get_sqlite_connection(str(path))
    assert database.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    database.campaigns.insert_one({"_id": "cmp_1", "updated_at": "now"})
    database.close()

    reopened = sqlite.get_sqlite_connection(str(path))
    assert reopened.campaigns.find_one({"_id": "cmp_1"})["updated_at"] == "now"
    reopened.close()


def test_transaction_rolls_back_all_tables() -> None:
    database = sqlite.get_sqlite_connection(":memory:")
    try:
        with database.transaction():
            database.campaigns.insert_one({"_id": "cmp_1"})
            database.cells.insert_one(
                {"_id": "cmp_1:cell_0_0", "campaign_id": "cmp_1", "cell_id": "cell_0_0"}
            )
            raise RuntimeError("stop")
    except RuntimeError:
        pass
    assert database.campaigns.count_documents({}) == 0
    assert database.cells.count_documents({}) == 0
    database.close()


def test_close_cached_database_is_idempotent(monkeypatch, tmp_path: Path) -> None:
    sqlite.close_sqlite_connection()
    monkeypatch.setattr(
        sqlite,
        "get_settings",
        lambda: type("Settings", (), {"sqlite_db_path": str(tmp_path / "cached.db")})(),
    )
    first = sqlite.get_database()
    assert sqlite.get_database() is first
    sqlite.close_sqlite_connection()
    sqlite.close_sqlite_connection()
    assert sqlite.get_database() is not first
    sqlite.close_sqlite_connection()
