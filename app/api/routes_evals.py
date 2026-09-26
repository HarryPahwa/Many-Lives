"""Evaluation routes (P0.5) (TDD §17.2): run, optimize, latest.

Scaffold: empty router; endpoints wired later.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])
