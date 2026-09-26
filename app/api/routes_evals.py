"""Evaluation routes (P0.5) — owned by Developer C.

Developer B supplies probe, metric, and optimizer functions in ``app.harness``.
TODO(C): bind these routes to A's canonical repository and B's probe runner.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])
