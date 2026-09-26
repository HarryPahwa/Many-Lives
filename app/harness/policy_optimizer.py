"""Closed-set, rule-based context-policy mutation."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.types import ActionClass, ContextPolicy, PolicyCreator, PolicyStatus


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


def decision(baseline: dict[str, object], candidate: dict[str, object]) -> str:
    """Pure B policy decision; A persists it transactionally and C triggers it."""

    return "PROMOTED" if promote_if_better(baseline, candidate) else "REJECTED"
