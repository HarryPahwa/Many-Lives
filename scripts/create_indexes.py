"""Create or verify the local SQLite schema and indexes."""

from __future__ import annotations

import sys

from dotenv import load_dotenv

from app.persistence.indexes import create_btree_indexes
from app.persistence.sqlite import close_sqlite_connection, get_database


def main() -> int:
    load_dotenv()
    try:
        db = get_database()
        names = create_btree_indexes(db)
        print(f"Created or verified {len(names)} B-tree indexes:")
        for name in names:
            print(f"- {name}")

        print("Memory vectors are stored as JSON and scored locally.")
        return 0
    except Exception as exc:
        print(f"Index creation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        close_sqlite_connection()


if __name__ == "__main__":
    raise SystemExit(main())
