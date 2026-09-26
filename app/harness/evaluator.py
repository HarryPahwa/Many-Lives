"""Probe aggregation and evaluation persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.harness.probes import PROBES, ProbeAssessment


def metrics_for(evaluation: dict[str, object]) -> dict[str, float]:
    runs = list(evaluation.get("runs", []))
    if not runs:
        return {"contradiction_rate": 0.0, "invented_entities": 0.0, "missing_context_rate": 0.0, "retrieval_hit_rate": 0.0, "mean_input_tokens": 0.0, "p50_latency_ms": 0.0, "p95_latency_ms": 0.0}
    count = len(runs)
    latencies = sorted(float(run.get("latency_ms", 0)) for run in runs)
    retrieval = [run["retrieval_hit"] for run in runs if run.get("retrieval_hit") is not None]
    return {
        "contradiction_rate": sum(float(run.get("contradictions", 0)) for run in runs) / count,
        "invented_entities": sum(float(run.get("invented_entities", 0)) for run in runs) / count,
        "missing_context_rate": sum(bool(run.get("missing_context", False)) for run in runs) / count,
        "retrieval_hit_rate": sum(retrieval) / len(retrieval) if retrieval else 0.0,
        "mean_input_tokens": sum(float(run.get("input_tokens", 0)) for run in runs) / count,
        "p50_latency_ms": latencies[(count - 1) // 2],
        "p95_latency_ms": latencies[min(count - 1, int(count * 0.95))],
    }


def run_suite(*, policy_version: int, probe_ids: list[str] | None, runs: int, db, runner) -> dict[str, object]:
    """Run the fixed probes with an injected canonical-engine runner."""

    chosen = probe_ids or sorted(PROBES)
    unknown = set(chosen) - set(PROBES)
    if unknown:
        raise ValueError(f"Unknown probes: {sorted(unknown)}")
    results: list[dict[str, object]] = []
    for probe_id in chosen:
        for run_number in range(runs):
            assessment: ProbeAssessment = PROBES[probe_id].assess(runner(PROBES[probe_id], run_number))
            results.append({"probe_id": probe_id, "run": run_number + 1, **assessment.__dict__})
    document: dict[str, object] = {
        "_id": f"eval_{uuid4().hex}",
        "policy_version": policy_version,
        "runs": results,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    document["metrics"] = metrics_for(document)
    db.evaluations.insert_one(document)
    return document
