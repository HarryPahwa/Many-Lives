import mongomock

from app.harness.evaluator import metrics_for, run_suite
from app.harness.policy_optimizer import apply, dominant_failure, promote_if_better, propose
from app.harness.context_policy import seed_context_policy


def test_metrics_and_suite_persist_all_p0_probes():
    db = mongomock.MongoClient().dungeon_test
    evaluation = run_suite(
        policy_version=1,
        probe_ids=["P01", "P14"],
        runs=2,
        db=db,
        runner=lambda probe, run: {"retrieval_hit": True, "input_tokens": 100, "latency_ms": 8},
    )
    assert len(evaluation["runs"]) == 4
    assert evaluation["metrics"]["retrieval_hit_rate"] == 1
    assert db.evaluations.count_documents({}) == 1


def test_optimizer_proposes_bounded_mutation_and_promotion_rule():
    policy = seed_context_policy()
    baseline = {"metrics": {"missing_context_rate": 1, "retrieval_hit_rate": 1, "contradiction_rate": 1, "mean_input_tokens": 100}}
    mutation = propose(policy, dominant_failure(baseline))
    candidate = apply(policy, mutation)
    assert candidate.version == 2
    assert candidate.rules[mutation.action_class].recent_event_window <= 15
    assert promote_if_better(
        {"metrics": {"contradiction_rate": 1, "mean_input_tokens": 100}},
        {"metrics": {"contradiction_rate": 0, "mean_input_tokens": 125}},
    )


def test_metrics_empty_evaluation_is_zeroed():
    assert metrics_for({"runs": []})["contradiction_rate"] == 0
