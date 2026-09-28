"""Structured turn-trace and pipeline logger (TDD §8, §13, §14).

Appends JSONL trace records for every turn to logs/turn_traces.jsonl for instant
offline debugging and candidate adjudication inspection.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def log_turn_trace(trace_record: dict[str, Any], log_file: str = "logs/turn_traces.jsonl") -> None:
    """Appends a structured trace record to the specified JSONL log file."""
    try:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(trace_record, default=str) + "\n")
    except Exception:
        # Trace logging failure must never fail the turn
        pass
