"""Evaluation routes (P0.5) — owned by Developer C.

Developer B supplies probe, metric, and optimizer functions in ``app.harness``.
TODO(C): bind these routes to A's canonical repository and B's probe runner.

The one route here today serves the P07 bounded-context measurement produced by
``scripts/seed_stress_history.py``. It is served verbatim from the artefact the
repository ships, so the page cannot show a number the file does not contain.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])

P07_RESULT_FILE = Path(__file__).parents[2] / "docs" / "p07_result.json"


@router.get("/bounded-context")
def bounded_context() -> dict[str, Any]:
    """Return the stored P07 run, or 404 when the measurement has not been run."""
    try:
        text = P07_RESULT_FILE.read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(
            status_code=404,
            detail="No bounded-context measurement; run scripts/seed_stress_history.py.",
        ) from None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=404, detail="The stored measurement could not be read."
        ) from None
