"""Create all configured MongoDB indexes (TDD §9, §12.5, A1)."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

from app.persistence.indexes import create_btree_indexes, ensure_memory_vector_index
from app.persistence.mongo import close_mongo_client, get_database


def _embedding_dimensions() -> int | None:
    raw = os.getenv("EMBEDDING_DIMS", "").strip()
    if not raw:
        return None
    try:
        dimensions = int(raw)
    except ValueError as exc:
        raise ValueError("EMBEDDING_DIMS must be a positive integer") from exc
    if dimensions <= 0:
        raise ValueError("EMBEDDING_DIMS must be a positive integer")
    return dimensions


def main() -> int:
    load_dotenv()
    try:
        db = get_database()
        names = create_btree_indexes(db)
        print(f"Created or verified {len(names)} B-tree indexes:")
        for name in names:
            print(f"- {name}")

        dimensions = _embedding_dimensions()
        if dimensions is None:
            print("Skipped memories_vector: EMBEDDING_DIMS is not configured.")
        else:
            ensure_memory_vector_index(db, dimensions)
            print("Created or verified memories_vector.")
        return 0
    except Exception as exc:
        print(f"Index creation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        close_mongo_client()


if __name__ == "__main__":
    raise SystemExit(main())
