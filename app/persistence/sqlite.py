"""SQLite connection lifecycle and small document-store adapter.

Canonical state is stored as JSON documents in ordinary SQLite tables.  The
adapter intentionally exposes the narrow collection API used by the existing
repository and harness layers; it is not a general MongoDB emulator.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from enum import IntEnum
import base64
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Iterator, TypeVar
from uuid import uuid4

from app.config import get_settings


T = TypeVar("T")
logger = logging.getLogger("many_lives.persistence.sqlite")
_logging_ready = False


def _configure_file_logging() -> None:
    global _logging_ready
    if _logging_ready:
        return
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_dir / "db.log", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    _logging_ready = True


class DuplicateKeyError(RuntimeError):
    """Raised when an insert violates a document or configured unique key."""


class ReturnDocument(IntEnum):
    BEFORE = 0
    AFTER = 1


class WriteResult:
    def __init__(self, matched_count: int = 0, deleted_count: int = 0) -> None:
        self.matched_count = matched_count
        self.modified_count = matched_count
        self.deleted_count = deleted_count


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _json_hook(value: dict[str, Any]) -> Any:
    if set(value) == {"$datetime"}:
        return datetime.fromisoformat(value["$datetime"])
    if set(value) == {"$bytes"}:
        return base64.b64decode(value["$bytes"])
    return value


def _dumps(document: dict[str, Any]) -> str:
    return json.dumps(document, default=_json_default, separators=(",", ":"))


def _loads(payload: str) -> dict[str, Any]:
    return json.loads(payload, object_hook=_json_hook)


def _value(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


def _set_value(document: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = document
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = deepcopy(value)


def _unset_value(document: dict[str, Any], path: str) -> None:
    parts = path.split(".")
    current: Any = document
    for part in parts[:-1]:
        if not isinstance(current, dict):
            return
        current = current.get(part)
    if isinstance(current, dict):
        current.pop(parts[-1], None)


_MISSING = object()


def _matches_value(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict) and any(str(k).startswith("$") for k in expected):
        for operator, operand in expected.items():
            if operator == "$in":
                if isinstance(actual, list):
                    if not any(item in operand for item in actual):
                        return False
                elif actual not in operand:
                    return False
            elif operator == "$ne":
                if actual == operand:
                    return False
            elif operator == "$exists":
                if (actual is not _MISSING) is not bool(operand):
                    return False
            elif operator == "$lt":
                if actual is _MISSING or not actual < operand:
                    return False
            elif operator == "$lte":
                if actual is _MISSING or not actual <= operand:
                    return False
            elif operator == "$gt":
                if actual is _MISSING or not actual > operand:
                    return False
            elif operator == "$gte":
                if actual is _MISSING or not actual >= operand:
                    return False
            else:
                raise NotImplementedError(f"Unsupported filter operator {operator}")
        return True
    if actual is _MISSING:
        return expected is None
    if isinstance(actual, list) and not isinstance(expected, list):
        return expected in actual
    return actual == expected


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, clause) for clause in expected):
                return False
        elif key == "$and":
            if not all(_matches(document, clause) for clause in expected):
                return False
        elif not _matches_value(_value(document, key), expected):
            return False
    return True


def _apply_update(document: dict[str, Any], update: dict[str, Any]) -> None:
    for operator, values in update.items():
        if operator == "$set":
            for path, value in values.items():
                _set_value(document, path, value)
        elif operator == "$inc":
            for path, amount in values.items():
                current = _value(document, path)
                _set_value(document, path, (0 if current is _MISSING else current) + amount)
        elif operator == "$unset":
            for path in values:
                _unset_value(document, path)
        elif operator == "$push":
            for path, value in values.items():
                current = _value(document, path)
                items = [] if current is _MISSING else list(current)
                items.extend(value["$each"] if isinstance(value, dict) and "$each" in value else [value])
                _set_value(document, path, items)
        elif operator == "$addToSet":
            for path, value in values.items():
                current = _value(document, path)
                items = [] if current is _MISSING else list(current)
                additions = value["$each"] if isinstance(value, dict) and "$each" in value else [value]
                items.extend(item for item in additions if item not in items)
                _set_value(document, path, items)
        else:
            raise NotImplementedError(f"Unsupported update operator {operator}")


class Cursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, key: str | list[tuple[str, int]], direction: int | None = None) -> "Cursor":
        keys = key if isinstance(key, list) else [(key, direction or 1)]
        for field, order in reversed(keys):
            self._documents.sort(
                key=lambda item: (_value(item, field) is _MISSING, _value(item, field)),
                reverse=order < 0,
            )
        return self

    def limit(self, count: int) -> "Cursor":
        self._documents = self._documents[:count]
        return self

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._documents)


_TABLES = (
    "campaigns", "cells", "entities", "events", "turns", "memories",
    "quests", "context_policies", "evaluations", "room_visuals", "visual_assets",
)


class Collection:
    def __init__(self, database: "SQLiteDatabase", name: str) -> None:
        if name not in _TABLES:
            raise KeyError(f"Unknown SQLite collection: {name}")
        self.database = database
        self.name = name

    def _documents(self) -> list[dict[str, Any]]:
        # A sqlite3 connection may be shared across FastAPI worker threads, but
        # a cursor operation on that connection must not overlap another one.
        # Copy every payload while holding the database lock, then decode after
        # releasing it so JSON work does not unnecessarily serialize readers.
        with self.database._lock:
            rows = self.database.connection.execute(
                f'SELECT data FROM "{self.name}"'
            ).fetchall()
            payloads = [row["data"] for row in rows]
        return [_loads(payload) for payload in payloads]

    def find(self, query: dict[str, Any] | None = None, projection: dict[str, int] | None = None, **_: Any) -> Cursor:
        documents = [doc for doc in self._documents() if _matches(doc, query or {})]
        if projection:
            included = {key for key, include in projection.items() if include}
            documents = [
                {key: value for key, value in doc.items() if key in included or key == "_id"}
                for doc in documents
            ]
        return Cursor(documents)

    def find_one(self, query: dict[str, Any] | None = None, *_, sort=None, **__) -> dict[str, Any] | None:
        cursor = self.find(query)
        if sort:
            cursor.sort(sort)
        return next(iter(cursor), None)

    def count_documents(self, query: dict[str, Any], **_: Any) -> int:
        return sum(1 for _ in self.find(query))

    def stored_json_bytes(self, query: dict[str, Any]) -> int:
        return sum(len(_dumps(document)) for document in self.find(query))

    def insert_one(self, document: dict[str, Any], **_: Any) -> WriteResult:
        document.setdefault("_id", f"{self.name}_{uuid4().hex}")
        self.database._write_document(self.name, document, replace=False)
        return WriteResult(1)

    def insert_many(self, documents: list[dict[str, Any]], **_: Any) -> WriteResult:
        for document in documents:
            self.insert_one(document)
        return WriteResult(len(documents))

    def replace_one(self, query: dict[str, Any], document: dict[str, Any], *, upsert=False, **_: Any) -> WriteResult:
        existing = self.find_one(query)
        if existing is None:
            if not upsert:
                return WriteResult()
            candidate = {**query, **document}
        else:
            candidate = deepcopy(document)
            candidate.setdefault("_id", existing["_id"])
        self.database._write_document(self.name, candidate, replace=existing is not None)
        return WriteResult(1)

    def update_one(self, query: dict[str, Any], update: dict[str, Any], *, upsert=False, **_: Any) -> WriteResult:
        existing = self.find_one(query)
        if existing is None:
            if not upsert:
                return WriteResult()
            candidate = {k: deepcopy(v) for k, v in query.items() if not k.startswith("$") and not isinstance(v, dict)}
            _apply_update(candidate, update)
            candidate.setdefault("_id", candidate.get("id"))
            if not candidate.get("_id"):
                raise ValueError("Upsert requires an _id")
            self.database._write_document(self.name, candidate, replace=False)
            return WriteResult(1)
        _apply_update(existing, update)
        self.database._write_document(self.name, existing, replace=True)
        return WriteResult(1)

    def update_many(self, query: dict[str, Any], update: dict[str, Any], **_: Any) -> WriteResult:
        documents = list(self.find(query))
        for document in documents:
            _apply_update(document, update)
            self.database._write_document(self.name, document, replace=True)
        return WriteResult(len(documents))

    def find_one_and_update(self, query: dict[str, Any], update: dict[str, Any], *, upsert=False, return_document=ReturnDocument.BEFORE, **_: Any) -> dict[str, Any] | None:
        with self.database.transaction():
            existing = self.find_one(query)
            before = deepcopy(existing)
            if existing is None:
                if not upsert:
                    return None
                candidate = {k: deepcopy(v) for k, v in query.items() if not k.startswith("$") and not isinstance(v, dict)}
                _apply_update(candidate, update)
                candidate.setdefault("_id", candidate.get("id"))
                self.database._write_document(self.name, candidate, replace=False)
                existing = candidate
            else:
                _apply_update(existing, update)
                self.database._write_document(self.name, existing, replace=True)
            return deepcopy(existing if return_document == ReturnDocument.AFTER else before)

    def delete_one(self, query: dict[str, Any]) -> WriteResult:
        document = self.find_one(query)
        if document is None:
            return WriteResult()
        with self.database._write_scope(self.name):
            self.database.connection.execute(
                f'DELETE FROM "{self.name}" WHERE id = ?', (document["_id"],)
            )
        return WriteResult(deleted_count=1)

    def delete_many(self, query: dict[str, Any]) -> WriteResult:
        documents = list(self.find(query))
        with self.database._write_scope(self.name):
            for document in documents:
                self.database.connection.execute(
                    f'DELETE FROM "{self.name}" WHERE id = ?', (document["_id"],)
                )
        return WriteResult(deleted_count=len(documents))

    def create_index(self, _keys, *, name: str, unique=False, **__: Any) -> str:
        # Physical indexes are initialized centrally; retain stable API names.
        return name

    def aggregate(self, pipeline: list[dict[str, Any]]) -> Cursor:
        documents = self._documents()
        for stage in pipeline:
            if "$match" in stage:
                documents = [doc for doc in documents if _matches(doc, stage["$match"])]
            elif "$group" in stage and "bytes" in stage["$group"]:
                documents = [{"_id": None, "bytes": sum(len(_dumps(doc)) for doc in documents)}]
            else:
                raise NotImplementedError("Use the SQLite memory retriever for vector queries")
        return Cursor(documents)


class SQLiteDatabase:
    """Thread-safe database handle shared by repositories and harness services."""

    def __init__(self, connection: sqlite3.Connection, path: str) -> None:
        self.connection = connection
        self.path = path
        self._lock = threading.RLock()
        self._local = threading.local()

    def __getitem__(self, name: str) -> Collection:
        return Collection(self, name)

    def __getattr__(self, name: str) -> Collection:
        if name in _TABLES:
            return self[name]
        raise AttributeError(name)

    @property
    def name(self) -> str:
        return Path(self.path).stem if self.path != ":memory:" else "memory"

    @property
    def in_transaction(self) -> bool:
        return bool(getattr(self._local, "depth", 0))

    @contextmanager
    def _write_scope(self, table: str):
        with self._lock:
            logger.debug("touch table=%s", table)
            try:
                yield
                if not self.in_transaction:
                    self.connection.commit()
            except Exception:
                if not self.in_transaction:
                    self.connection.rollback()
                logger.exception("SQLite write failed table=%s", table)
                raise

    def _write_document(self, table: str, document: dict[str, Any], *, replace: bool) -> None:
        document_id = document.get("_id")
        if not isinstance(document_id, str) or not document_id:
            raise ValueError(f"{table} document requires a string _id")
        columns = {
            "campaign_id": document.get("campaign_id"),
            "cell_id": document.get("cell_id"),
            "entity_id": document.get("entity_id"),
            "entity_type": document.get("entity_type"),
            "location_ref": _value(document, "location.ref_id"),
            "turn_sequence": document.get("turn_sequence"),
            "event_index": document.get("event_index"),
            "memory_id": document.get("memory_id"),
            "updated_at": str(document.get("updated_at") or document.get("created_at") or ""),
        }
        if columns["location_ref"] is _MISSING:
            columns["location_ref"] = None
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        sql = f'''{verb} INTO "{table}"
            (id, campaign_id, cell_id, entity_id, entity_type, location_ref,
             turn_sequence, event_index, memory_id, updated_at, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'''
        params = (document_id, *columns.values(), _dumps(document))
        with self._write_scope(table):
            try:
                self.connection.execute(sql, params)
            except sqlite3.IntegrityError as exc:
                raise DuplicateKeyError(str(exc)) from exc

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            depth = getattr(self._local, "depth", 0)
            savepoint = f"nested_{depth}"
            logger.debug("transaction begin depth=%d", depth)
            if depth == 0:
                self.connection.execute("BEGIN IMMEDIATE")
            else:
                self.connection.execute(f"SAVEPOINT {savepoint}")
            self._local.depth = depth + 1
            try:
                yield self.connection
                if depth == 0:
                    self.connection.commit()
                else:
                    self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                logger.debug("transaction commit depth=%d", depth)
            except Exception:
                if depth == 0:
                    self.connection.rollback()
                else:
                    self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                logger.exception("transaction rollback depth=%d", depth)
                raise
            finally:
                self._local.depth = depth

    def run_transaction(self, callback: Callable[[sqlite3.Connection], T]) -> T:
        with self.transaction() as connection:
            return callback(connection)

    def close(self) -> None:
        self.connection.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    id TEXT PRIMARY KEY,
    campaign_id TEXT,
    cell_id TEXT,
    entity_id TEXT,
    entity_type TEXT,
    location_ref TEXT,
    turn_sequence INTEGER,
    event_index INTEGER,
    memory_id TEXT,
    updated_at TEXT,
    data TEXT NOT NULL CHECK(json_valid(data))
);
"""


def _initialize(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    for table in _TABLES:
        connection.executescript(SCHEMA.format(table=table))
        connection.execute(
            f'CREATE INDEX IF NOT EXISTS "{table}_campaign" ON "{table}" (campaign_id)'
        )
    connection.executescript("""
        CREATE UNIQUE INDEX IF NOT EXISTS cells_campaign_cell ON cells(campaign_id, cell_id);
        CREATE UNIQUE INDEX IF NOT EXISTS entities_campaign_entity ON entities(campaign_id, entity_id);
        CREATE INDEX IF NOT EXISTS entities_location ON entities(campaign_id, location_ref, entity_type);
        CREATE UNIQUE INDEX IF NOT EXISTS events_sequence ON events(campaign_id, turn_sequence, event_index);
        CREATE UNIQUE INDEX IF NOT EXISTS turns_campaign_id ON turns(campaign_id, id);
        CREATE UNIQUE INDEX IF NOT EXISTS memories_campaign_memory ON memories(campaign_id, memory_id);
    """)
    connection.commit()


def get_sqlite_connection(db_path: str | None = None) -> SQLiteDatabase:
    """Open and initialize a SQLite database; ``:memory:`` is fully supported."""
    _configure_file_logging()
    path = db_path or get_settings().sqlite_db_path
    if path != ":memory:":
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        path = str(Path(path).expanduser())
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    _initialize(connection)
    return SQLiteDatabase(connection, path)


_database: SQLiteDatabase | None = None
_database_lock = threading.Lock()


def get_database() -> SQLiteDatabase:
    global _database
    with _database_lock:
        if _database is None:
            _database = get_sqlite_connection()
        return _database


def close_sqlite_connection() -> None:
    global _database
    with _database_lock:
        database, _database = _database, None
    if database is not None:
        database.close()


def with_transaction(database: SQLiteDatabase, callback: Callable[[sqlite3.Connection], T]) -> T:
    return database.run_transaction(callback)
