"""Closed-set, rule-based context-policy mutation."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.types import ActionClass, ContextPolicy, PolicyCreator, PolicyStatus
from app.harness.evaluator import run_suite


@dataclass(frozen=True)
class Mutation:
    kind: str
    action_class: ActionClass
    value: object
    component: str | None = None


def dominant_failure(evaluation: dict[str, object]) -> str | None:
    metrics = evaluation.get("metrics", {})
    if metrics.get("missing_context_rate", 0):
        return "MISSING_RELATIONSHIP_CONTEXT"
    if metrics.get("retrieval_hit_rate", 1) < 1:
        return "RETRIEVAL_MISS"
    if metrics.get("contradiction_rate", 0):
        return "CONTRADICTION"
    if metrics.get("mean_input_tokens", 0) > 3000:
        return "HIGH_TOKENS"
    return None


def propose(policy: ContextPolicy, failure: str | None) -> Mutation | None:
    if failure == "MISSING_RELATIONSHIP_CONTEXT":
        rule = policy.rules[ActionClass.SOCIAL]
        return Mutation("SET_RECENT_EVENT_WINDOW", ActionClass.SOCIAL, min(15, rule.recent_event_window + 5))
    if failure == "RETRIEVAL_MISS":
        rule = policy.rules[ActionClass.SOCIAL]
        return Mutation("SET_TOP_K", ActionClass.SOCIAL, min(6, rule.vector_memory.top_k + 1))
    if failure == "HIGH_TOKENS":
        rule = policy.rules[ActionClass.SOCIAL]
        return Mutation("SET_TOP_K", ActionClass.SOCIAL, max(0, rule.vector_memory.top_k - 1))
    if failure == "CONTRADICTION":
        return Mutation("SET_COMPONENT_MODE", ActionClass.SOCIAL, "mandatory", component="player_inventory")
    return None


def apply(policy: ContextPolicy, mutation: Mutation) -> ContextPolicy:
    copy = ContextPolicy.model_validate(policy.model_dump(by_alias=True, mode="json"))
    rule = copy.rules[mutation.action_class]
    if mutation.kind == "SET_RECENT_EVENT_WINDOW":
        rule.recent_event_window = int(mutation.value)
    elif mutation.kind == "SET_TOP_K":
        rule.vector_memory.top_k = int(mutation.value)
    elif mutation.kind == "SET_COMPONENT_MODE" and mutation.component:
        for collection in (rule.mandatory, rule.conditional):
            if mutation.component in collection:
                collection.remove(mutation.component)
        if mutation.value == "mandatory":
            rule.mandatory.append(mutation.component)
        elif mutation.value == "conditional":
            rule.conditional.append(mutation.component)
    else:
        raise ValueError(f"Unsupported mutation {mutation.kind}")
    copy.version += 1
    copy.policy_id = f"context_policy_v{copy.version}"
    copy.parent_version = policy.version
    copy.status = PolicyStatus.CANDIDATE
    copy.created_by = PolicyCreator.OPTIMIZER
    return copy


def promote_if_better(baseline: dict[str, object], candidate: dict[str, object]) -> bool:
    before = baseline["metrics"]
    after = candidate["metrics"]
    return (
        after["contradiction_rate"] < before["contradiction_rate"]
        and after["mean_input_tokens"] <= 1.25 * before["mean_input_tokens"]
    )


def optimize(*, db, policy: ContextPolicy, probe_ids: list[str] | None, runs: int, runner) -> dict[str, object]:
    """Run baseline/candidate suites and atomically retain only an improvement."""

    baseline = run_suite(
        policy_version=policy.version, probe_ids=probe_ids, runs=runs, db=db, runner=runner
    )
    mutation = propose(policy, dominant_failure(baseline))
    if mutation is None:
        return {"decision": "NO_MUTATION", "baseline": baseline["_id"]}
    candidate_policy = apply(policy, mutation)
    candidate = run_suite(
        policy_version=candidate_policy.version,
        probe_ids=probe_ids,
        runs=runs,
        db=db,
        runner=runner,
    )
    promoted = promote_if_better(baseline, candidate)
    candidate_policy.status = PolicyStatus.ACTIVE if promoted else PolicyStatus.REJECTED
    db.context_policies.replace_one({"_id": candidate_policy.policy_id}, candidate_policy.model_dump(by_alias=True, mode="json"), upsert=True)
    if promoted:
        db.context_policies.update_one({"_id": policy.policy_id}, {"$set": {"status": PolicyStatus.RETIRED.value}})
        db.campaigns.update_many({"status": "ACTIVE"}, {"$set": {"active_context_policy_version": candidate_policy.version}})
    return {
        "decision": "PROMOTED" if promoted else "REJECTED",
        "baseline": baseline["_id"],
        "candidate": candidate["_id"],
        "mutation": mutation.__dict__,
    }
