"""Synchronous P0.5 evaluation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pymongo import MongoClient

from app.api.schemas import EvaluationRequest
from app.config import get_settings
from app.domain.types import ContextPolicy
from app.harness.evaluator import run_suite
from app.harness.context_policy import seed_context_policy
from app.harness.policy_optimizer import optimize
from app.services.stubs import get_engine

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])


def _runtime():
    engine = get_engine()
    settings = get_settings()
    if not engine.CANONICAL_EVENTS or not settings.mongodb_uri:
        raise HTTPException(status_code=503, detail="Evaluations require the canonical Mongo engine.")
    runner = getattr(engine, "run_probe", None)
    if runner is None:
        raise HTTPException(status_code=503, detail="Canonical probe runner is unavailable.")
    return MongoClient(settings.mongodb_uri)[settings.mongodb_db], runner


@router.post("/run")
def run_evaluation(body: EvaluationRequest) -> dict:
    db, runner = _runtime()
    return run_suite(
        policy_version=body.policy_version,
        probe_ids=body.probe_ids,
        runs=body.runs,
        db=db,
        runner=runner,
    )


@router.post("/optimize")
def optimize_evaluation(body: EvaluationRequest) -> dict:
    db, runner = _runtime()
    document = db.context_policies.find_one({"version": body.policy_version})
    policy = seed_context_policy() if document is None else ContextPolicy.model_validate(document)
    return optimize(db=db, policy=policy, probe_ids=body.probe_ids, runs=body.runs, runner=runner)


@router.get("/{evaluation_id}")
def get_evaluation(evaluation_id: str) -> dict:
    db, _ = _runtime()
    document = db.evaluations.find_one({"_id": evaluation_id})
    if document is None:
        raise HTTPException(status_code=404, detail="Unknown evaluation.")
    return document
