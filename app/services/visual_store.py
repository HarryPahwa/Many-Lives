"""Persistence for room visuals (Room Visuals §8).

Three interchangeable stores — memory, file, Mongo — chosen to match whatever
durability the world engine already has. Nothing here touches campaign, cell,
entity, event, memory or turn data (VIS-01); it owns only its own records and
image bytes.

The atomic claim (§8.4) is what keeps at most one generation per cell in
flight. Each backend implements it with the strongest primitive it has.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: Path segments are built from these, so they are validated before use.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]+$")
_ASSET_ID = re.compile(r"^va_[0-9a-f]{32}$")

STATUS_NONE = "NONE"
STATUS_GENERATING = "GENERATING"
STATUS_READY = "READY"
STATUS_FAILED = "FAILED"

#: A process killed mid-generation leaves a claim behind; it becomes
#: reclaimable after this long (§8.4) [DEFAULT].
STALE_AFTER_S = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_asset_id() -> str:
    return "va_" + uuid.uuid4().hex


def is_valid_asset_id(asset_id: str) -> bool:
    return bool(_ASSET_ID.match(asset_id or ""))


def safe_segment(value: str) -> bool:
    return bool(_SAFE_ID.match(value or ""))


@dataclass
class VisualRecord:
    """One per (campaign, cell) — the current state of that room's picture."""

    campaign_id: str
    cell_id: str
    status: str = STATUS_NONE
    revision: int = 0
    signature: str | None = None
    spec: dict[str, Any] | None = None
    current_asset_id: str | None = None
    base_asset_id: str | None = None
    edits_since_base: int = 0
    generation_started_at: str | None = None
    error_code: str | None = None
    model: str | None = None
    updated_at: str = field(default_factory=lambda: _now().isoformat())


@dataclass
class VisualAsset:
    asset_id: str
    campaign_id: str
    cell_id: str
    revision: int
    signature: str | None
    media_type: str
    data: bytes
    size: int
    model: str | None
    kind: str  # GENERATE | EDIT
    created_at: str = field(default_factory=lambda: _now().isoformat())


class _BaseStore:
    """Shared claim semantics; subclasses provide storage."""

    def get(self, campaign_id: str, cell_id: str) -> VisualRecord | None:
        raise NotImplementedError

    def put(self, record: VisualRecord) -> None:
        raise NotImplementedError

    def claim(
        self, campaign_id: str, cell_id: str, stale_after_s: int = STALE_AFTER_S
    ) -> VisualRecord | None:
        raise NotImplementedError

    def put_asset(self, asset: VisualAsset) -> None:
        raise NotImplementedError

    def get_asset(self, campaign_id: str, asset_id: str) -> VisualAsset | None:
        raise NotImplementedError

    def prune(self, campaign_id: str, cell_id: str, keep: int) -> int:
        raise NotImplementedError

    # -- helpers shared by every backend --

    @staticmethod
    def _is_stale(record: VisualRecord, stale_after_s: int) -> bool:
        if record.status != STATUS_GENERATING:
            return True
        started = record.generation_started_at
        if not started:
            return True
        try:
            when = datetime.fromisoformat(started)
        except ValueError:
            return True
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return _now() - when > timedelta(seconds=stale_after_s)


class MemoryVisualStore(_BaseStore):
    """For tests and for runs whose world state is also in memory."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[tuple[str, str], VisualRecord] = {}
        self._assets: dict[str, VisualAsset] = {}

    def get(self, campaign_id: str, cell_id: str) -> VisualRecord | None:
        with self._lock:
            found = self._records.get((campaign_id, cell_id))
            return VisualRecord(**asdict(found)) if found else None

    def put(self, record: VisualRecord) -> None:
        with self._lock:
            record.updated_at = _now().isoformat()
            self._records[(record.campaign_id, record.cell_id)] = VisualRecord(
                **asdict(record)
            )

    def claim(
        self, campaign_id: str, cell_id: str, stale_after_s: int = STALE_AFTER_S
    ) -> VisualRecord | None:
        with self._lock:
            key = (campaign_id, cell_id)
            existing = self._records.get(key)
            if existing is not None and not self._is_stale(existing, stale_after_s):
                return None
            record = existing or VisualRecord(campaign_id=campaign_id, cell_id=cell_id)
            record.status = STATUS_GENERATING
            record.generation_started_at = _now().isoformat()
            record.error_code = None
            record.updated_at = _now().isoformat()
            self._records[key] = record
            return VisualRecord(**asdict(record))

    def put_asset(self, asset: VisualAsset) -> None:
        with self._lock:
            self._assets[asset.asset_id] = asset

    def get_asset(self, campaign_id: str, asset_id: str) -> VisualAsset | None:
        with self._lock:
            asset = self._assets.get(asset_id)
            # Campaign scoping (VIS-06): an id from another campaign is a miss,
            # not a cross-campaign read.
            if asset is None or asset.campaign_id != campaign_id:
                return None
            return asset

    def prune(self, campaign_id: str, cell_id: str, keep: int) -> int:
        with self._lock:
            record = self._records.get((campaign_id, cell_id))
            protected = {
                record.current_asset_id if record else None,
                record.base_asset_id if record else None,
            }
            candidates = sorted(
                (
                    a
                    for a in self._assets.values()
                    if a.campaign_id == campaign_id and a.cell_id == cell_id
                ),
                key=lambda a: a.revision,
                reverse=True,
            )
            removed = 0
            for asset in candidates[keep:]:
                if asset.asset_id in protected:
                    continue
                self._assets.pop(asset.asset_id, None)
                removed += 1
            return removed


class FileVisualStore(_BaseStore):
    """JSON index plus one file per image; durable without Atlas."""

    def __init__(self, root: str) -> None:
        self._root = Path(root)
        self._index_path = self._root / "index.json"
        self._lock = threading.Lock()
        self._root.mkdir(parents=True, exist_ok=True)

    # -- index i/o (always called under the lock) --

    def _read(self) -> dict[str, Any]:
        if not self._index_path.exists():
            return {"records": {}, "assets": {}}
        try:
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A damaged index must not take the server down mid-demo; the
            # images on disk are orphaned but nothing else breaks.
            return {"records": {}, "assets": {}}

    def _write(self, payload: dict[str, Any]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        tmp.replace(self._index_path)

    def _asset_path(self, campaign_id: str, asset_id: str) -> Path:
        # Both segments are validated before they ever reach the filesystem.
        if not safe_segment(campaign_id) or not is_valid_asset_id(asset_id):
            raise ValueError("unsafe identifier")
        return self._root / campaign_id / f"{asset_id}.img"

    def get(self, campaign_id: str, cell_id: str) -> VisualRecord | None:
        with self._lock:
            raw = self._read()["records"].get(f"{campaign_id}:{cell_id}")
            return VisualRecord(**raw) if raw else None

    def put(self, record: VisualRecord) -> None:
        with self._lock:
            payload = self._read()
            record.updated_at = _now().isoformat()
            payload["records"][f"{record.campaign_id}:{record.cell_id}"] = asdict(record)
            self._write(payload)

    def claim(
        self, campaign_id: str, cell_id: str, stale_after_s: int = STALE_AFTER_S
    ) -> VisualRecord | None:
        with self._lock:
            payload = self._read()
            key = f"{campaign_id}:{cell_id}"
            raw = payload["records"].get(key)
            existing = VisualRecord(**raw) if raw else None
            if existing is not None and not self._is_stale(existing, stale_after_s):
                return None
            record = existing or VisualRecord(campaign_id=campaign_id, cell_id=cell_id)
            record.status = STATUS_GENERATING
            record.generation_started_at = _now().isoformat()
            record.error_code = None
            record.updated_at = _now().isoformat()
            payload["records"][key] = asdict(record)
            self._write(payload)
            return VisualRecord(**asdict(record))

    def put_asset(self, asset: VisualAsset) -> None:
        path = self._asset_path(asset.campaign_id, asset.asset_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_bytes(asset.data)
        tmp.replace(path)
        with self._lock:
            payload = self._read()
            meta = asdict(asset)
            meta.pop("data")
            payload["assets"][asset.asset_id] = meta
            self._write(payload)

    def get_asset(self, campaign_id: str, asset_id: str) -> VisualAsset | None:
        if not is_valid_asset_id(asset_id) or not safe_segment(campaign_id):
            return None
        with self._lock:
            meta = self._read()["assets"].get(asset_id)
        if meta is None or meta.get("campaign_id") != campaign_id:
            return None
        try:
            data = self._asset_path(campaign_id, asset_id).read_bytes()
        except (OSError, ValueError):
            return None
        return VisualAsset(**{**meta, "data": data})

    def prune(self, campaign_id: str, cell_id: str, keep: int) -> int:
        with self._lock:
            payload = self._read()
            raw = payload["records"].get(f"{campaign_id}:{cell_id}")
            record = VisualRecord(**raw) if raw else None
            protected = {
                record.current_asset_id if record else None,
                record.base_asset_id if record else None,
            }
            candidates = sorted(
                (
                    m
                    for m in payload["assets"].values()
                    if m.get("campaign_id") == campaign_id
                    and m.get("cell_id") == cell_id
                ),
                key=lambda m: m.get("revision", 0),
                reverse=True,
            )
            removed = 0
            for meta in candidates[keep:]:
                asset_id = meta["asset_id"]
                if asset_id in protected:
                    continue
                payload["assets"].pop(asset_id, None)
                try:
                    self._asset_path(campaign_id, asset_id).unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass
                removed += 1
            if removed:
                self._write(payload)
            return removed


class MongoVisualStore(_BaseStore):
    """Atlas-backed store. Uses only its own two collections."""

    def __init__(self, database: Any) -> None:
        self._db = database
        self._records = database["room_visuals"]
        self._assets = database["visual_assets"]
        self._indexed = False

    def _ensure_indexes(self) -> None:
        if self._indexed:
            return
        # Lazily, and idempotently: app/persistence/indexes.py belongs to
        # Developer A and this feature must not edit it.
        self._records.create_index(
            [("campaign_id", 1), ("cell_id", 1)], unique=True, name="visual_cell"
        )
        self._assets.create_index(
            [("campaign_id", 1), ("cell_id", 1), ("revision", -1)], name="visual_rev"
        )
        self._indexed = True

    @staticmethod
    def _to_record(doc: dict[str, Any] | None) -> VisualRecord | None:
        if not doc:
            return None
        fields = {k: v for k, v in doc.items() if k != "_id"}
        known = {f for f in VisualRecord.__dataclass_fields__}
        return VisualRecord(**{k: v for k, v in fields.items() if k in known})

    def get(self, campaign_id: str, cell_id: str) -> VisualRecord | None:
        self._ensure_indexes()
        return self._to_record(
            self._records.find_one({"campaign_id": campaign_id, "cell_id": cell_id})
        )

    def put(self, record: VisualRecord) -> None:
        self._ensure_indexes()
        record.updated_at = _now().isoformat()
        self._records.update_one(
            {"_id": f"{record.campaign_id}:{record.cell_id}"},
            {"$set": asdict(record)},
            upsert=True,
        )

    def claim(
        self, campaign_id: str, cell_id: str, stale_after_s: int = STALE_AFTER_S
    ) -> VisualRecord | None:
        self._ensure_indexes()
        from pymongo import ReturnDocument
        from pymongo.errors import DuplicateKeyError

        now = _now()
        cutoff = (now - timedelta(seconds=stale_after_s)).isoformat()
        try:
            doc = self._records.find_one_and_update(
                {
                    "_id": f"{campaign_id}:{cell_id}",
                    "$or": [
                        {"status": {"$ne": STATUS_GENERATING}},
                        {"generation_started_at": {"$lt": cutoff}},
                        {"generation_started_at": None},
                    ],
                },
                {
                    "$set": {
                        "campaign_id": campaign_id,
                        "cell_id": cell_id,
                        "status": STATUS_GENERATING,
                        "generation_started_at": now.isoformat(),
                        "error_code": None,
                        "updated_at": now.isoformat(),
                    }
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            # The filter did not match because a live claim exists; the upsert
            # then collided on _id. That collision *is* the "already running"
            # answer (§8.4).
            return None
        return self._to_record(doc)

    def put_asset(self, asset: VisualAsset) -> None:
        self._ensure_indexes()
        from bson.binary import Binary

        doc = asdict(asset)
        doc["_id"] = asset.asset_id
        doc["data"] = Binary(asset.data)
        self._assets.replace_one({"_id": asset.asset_id}, doc, upsert=True)

    def get_asset(self, campaign_id: str, asset_id: str) -> VisualAsset | None:
        self._ensure_indexes()
        if not is_valid_asset_id(asset_id):
            return None
        doc = self._assets.find_one({"_id": asset_id, "campaign_id": campaign_id})
        if not doc:
            return None
        known = {f for f in VisualAsset.__dataclass_fields__}
        fields = {k: v for k, v in doc.items() if k in known}
        fields["data"] = bytes(doc.get("data") or b"")
        return VisualAsset(**fields)

    def prune(self, campaign_id: str, cell_id: str, keep: int) -> int:
        self._ensure_indexes()
        record = self.get(campaign_id, cell_id)
        protected = {
            record.current_asset_id if record else None,
            record.base_asset_id if record else None,
        }
        docs = list(
            self._assets.find(
                {"campaign_id": campaign_id, "cell_id": cell_id}, {"_id": 1, "revision": 1}
            ).sort("revision", -1)
        )
        removed = 0
        for doc in docs[keep:]:
            if doc["_id"] in protected:
                continue
            self._assets.delete_one({"_id": doc["_id"]})
            removed += 1
        return removed


# ---------------------------------------------------------------------------
# Selection (§8.1)
# ---------------------------------------------------------------------------

_STORE: _BaseStore | None = None
_STORE_LOCK = threading.Lock()


def build_store() -> _BaseStore:
    """Choose a store to match the durability the engine already has."""
    from app.config import get_settings

    settings = get_settings()
    choice = (settings.visual_store or "auto").lower()

    if choice == "auto":
        if settings.mongodb_uri and settings.mongodb_db:
            choice = "mongo"
        elif os.getenv("STUB_STATE_FILE", "").strip():
            choice = "file"
        else:
            choice = "memory"

    if choice == "mongo":
        from app.persistence.mongo import get_database

        return MongoVisualStore(get_database())
    if choice == "file":
        return FileVisualStore(settings.visuals_dir)
    return MemoryVisualStore()


def get_store() -> _BaseStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = build_store()
        return _STORE


def set_store(store: _BaseStore | None) -> None:
    """Test hook; passing None forces a rebuild on next use."""
    global _STORE
    with _STORE_LOCK:
        _STORE = store
