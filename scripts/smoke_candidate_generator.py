"""Opt-in live probe for candidate-generator behavior.

This compares the legacy one-step generator with the two-step outcome/compiler
pipeline. It does not start the API, read or write SQLite, or commit mutations.

Run only when a real ``OPENROUTER_API_KEY`` and ``MODEL_ADJUDICATOR`` are set:

    uv run python scripts/smoke_candidate_generator.py --runs 3
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.config import get_settings
from app.domain.mutation_validator import validate_candidate_bundle
from app.domain.mutations import AppendEvent, MutateAttribute, MutationBundle
from app.domain.picker import select_winning_candidate
from app.domain.rng import TurnRng
from app.domain.types import ActionClass, EventType
from app.harness.candidate_generator import ModelCandidateGenerator, load_candidate_count
from app.harness.jev_scorer import OpenRouterJevScorer, load_runtime_rules
from app.harness.model_client import OpenRouterModelClient
from app.harness.model_client import ModelOutputError
from app.harness.mutation_compiler import ModelMutationCompiler, validate_compilation_fidelity
from app.harness.outcome_generator import (
    ModelOutcomeGenerator,
    mentioned_target_ids,
    validate_outcome,
)


@dataclass(frozen=True)
class MockWorldSnapshot:
    """Minimal canonical view required by deterministic candidate validation."""

    campaign: Mapping[str, Any]
    player: Mapping[str, Any]
    current_cell: Mapping[str, Any]
    destination_cell: Mapping[str, Any] | None
    characters: Sequence[Mapping[str, Any]]
    items: Sequence[Mapping[str, Any]]
    container_items: Sequence[Mapping[str, Any]]
    owned_items: Sequence[Mapping[str, Any]]


def mock_world() -> tuple[MockWorldSnapshot, dict[str, Any]]:
    """Return matching validator and model projections for a fixed encounter."""

    player = {
        "id": "player_1",
        "entity_id": "player_1",
        "name": "Ada",
        "entity_type": "PLAYER",
        "version": 1,
        "stats": {"hp": 20, "max_hp": 20, "attack": 5, "defense": 3, "skill": 4},
        "physical_conditions": [],
        "mental_conditions": [],
    }
    keeper = {
        "id": "keeper_1",
        "entity_id": "keeper_1",
        "name": "Dust-worn Keeper",
        "entity_type": "NPC",
        "version": 1,
        "status": "ALIVE",
        "disposition": "NEUTRAL",
        "stats": {"hp": 12, "max_hp": 12, "attack": 2, "defense": 2, "skill": 3},
        "physical_conditions": [],
        "mental_conditions": [],
        "knowledge": [],
    }
    goblin = {
        "id": "goblin_1",
        "entity_id": "goblin_1",
        "name": "Tunnel Goblin",
        "entity_type": "ENEMY",
        "version": 1,
        "status": "ALIVE",
        "disposition": "HOSTILE",
        "stats": {"hp": 14, "max_hp": 14, "attack": 4, "defense": 3, "skill": 3},
        "physical_conditions": [],
        "mental_conditions": [],
    }
    cell = {
        "id": "cell_0_0",
        "cell_id": "cell_0_0",
        "name": "Dustbound Archive",
        "version": 1,
        "description": "A worn stone archive with low arches and clear footing.",
    }
    canonical = MockWorldSnapshot(
        campaign={"id": "smoke_campaign", "current_turn": 1, "version": 1},
        player=player,
        current_cell=cell,
        destination_cell=None,
        characters=[keeper, goblin],
        items=[],
        container_items=[],
        owned_items=[],
    )
    model_world = {
        "campaign": canonical.campaign,
        "player": player,
        "current_cell": cell,
        "characters": [keeper, goblin],
        "items": [],
        "container_items": [],
        "owned_items": [],
        "context": (
            "Both characters are visible. The keeper knows no supplied world facts. "
            "The goblin is alert, hostile, and able to defend or counterattack."
        ),
    }
    return canonical, model_world


def _dialogue_events(bundle: MutationBundle) -> list[AppendEvent]:
    return [
        mutation
        for mutation in bundle.mutations
        if isinstance(mutation, AppendEvent) and mutation.event_type == EventType.DIALOGUE
    ]


def has_spoken_opening(bundle: MutationBundle) -> bool:
    """Recognize the response modes the prompt defines as explicitly spoken."""

    spoken_markers = ("SPOKEN", "GREETING", "QUESTION", "REFUSAL", "REPLY", "ANSWER")
    return any(
        any(
            marker
            in " ".join(
                str(event.payload.get(field, "")).upper()
                for field in ("dialogue_intent", "npc_reaction")
            )
            for marker in spoken_markers
        )
        for event in _dialogue_events(bundle)
    )


def has_counterattack(bundle: MutationBundle) -> bool:
    """A mechanical counterattack must target or reduce the player's HP."""

    for mutation in bundle.mutations:
        if isinstance(mutation, AppendEvent) and mutation.event_type == EventType.ATTACK_RESOLVED:
            if mutation.payload.get("target_id") == "player_1":
                return True
        if (
            isinstance(mutation, MutateAttribute)
            and mutation.target_id == "player_1"
            and mutation.path == "stats.hp"
            and mutation.op == "ADD"
            and isinstance(mutation.value, (int, float))
            and mutation.value < 0
        ):
            return True
    return False


def summarize(
    action: str,
    candidates: list[MutationBundle],
    world: MockWorldSnapshot,
) -> dict[str, Any]:
    rows = []
    for candidate in candidates:
        valid, rejection = validate_candidate_bundle(world, candidate)
        dialogue = _dialogue_events(candidate)
        rows.append(
            {
                "bundle_id": candidate.bundle_id,
                "description": candidate.action_description,
                "valid": valid,
                "rejection": rejection,
                "dialogue": bool(dialogue),
                "npc_reactions": [event.payload.get("npc_reaction") for event in dialogue],
                "spoken_opening": has_spoken_opening(candidate),
                "counterattack": has_counterattack(candidate),
            }
        )
    return {
        "action": action,
        "candidate_count": len(rows),
        "valid_count": sum(row["valid"] for row in rows),
        "dialogue_count": sum(row["dialogue"] for row in rows),
        "spoken_opening_count": sum(row["spoken_opening"] for row in rows),
        "counterattack_count": sum(row["counterattack"] for row in rows),
        "candidates": rows,
    }


def _usage(result: Any) -> dict[str, Any]:
    if result is None:
        return {"model": None, "input_tokens": 0, "output_tokens": 0, "latency_ms": 0}
    return {
        "model": result.model,
        "input_tokens": result.usage.get("input_tokens", 0),
        "output_tokens": result.usage.get("output_tokens", 0),
        "latency_ms": result.latency_ms,
        "attempts": result.attempts,
    }


def run_one_step(
    action: str,
    world: MockWorldSnapshot,
    model_world: dict[str, Any],
    generator: ModelCandidateGenerator,
    scorer: OpenRouterJevScorer,
    candidate_count: int,
) -> dict[str, Any]:
    generated = generator.generate_candidates(action, model_world, candidate_count=candidate_count)
    valid = [
        candidate
        for candidate in generated.candidates
        if validate_candidate_bundle(world, candidate)[0]
    ]
    scoring = scorer.score_candidates(action, valid, model_world, load_runtime_rules())
    winner = select_winning_candidate(
        valid,
        scoring.normalized_weights(),
        TurnRng(42, 1),
    )
    report = summarize(action, generated.candidates, world)
    report.update(
        {
            "mode": "one_step",
            "selected_id": winner.bundle_id,
            "selected_bundle_valid": validate_candidate_bundle(world, winner)[0],
            "model_calls": [_usage(generator.last_result)],
            "jev_latency_ms": scorer.last_latency_ms,
            "jev_usage": scorer.last_usage,
        }
    )
    return report


def run_two_step(
    action: str,
    action_class: ActionClass,
    world: MockWorldSnapshot,
    model_world: dict[str, Any],
    generator: ModelOutcomeGenerator,
    compiler: ModelMutationCompiler,
    scorer: OpenRouterJevScorer,
    candidate_count: int,
) -> dict[str, Any]:
    target_ids = mentioned_target_ids(action, model_world)
    generated = generator.generate_outcomes(
        action,
        model_world,
        actor_id="player_1",
        action_class=action_class,
        required_target_ids=target_ids,
        candidate_count=candidate_count,
    )
    rows = []
    valid = []
    for candidate in generated.candidates:
        is_valid, rejection = validate_outcome(
            candidate,
            model_world,
            actor_id="player_1",
            action_class=action_class,
            required_target_ids=target_ids,
        )
        rows.append(
            {
                "bundle_id": candidate.bundle_id,
                "description": candidate.action_description,
                "valid": is_valid,
                "rejection": rejection,
                "consequences": [item.model_dump(mode="json") for item in candidate.consequences],
            }
        )
        if is_valid:
            valid.append(candidate)
    if not valid:
        return {
            "mode": "two_step",
            "action": action,
            "candidate_count": len(rows),
            "valid_count": 0,
            "selected_id": None,
            "selected_bundle_valid": False,
            "failure": "NO_VALID_OUTCOMES",
            "candidates": rows,
            "model_calls": [_usage(generator.last_result)],
        }
    scoring = scorer.score_candidates(action, valid, model_world, load_runtime_rules())
    selected = select_winning_candidate(valid, scoring.normalized_weights(), TurnRng(42, 1))
    try:
        bundle = compiler.compile(selected, model_world)
    except ModelOutputError as exc:
        return {
            "mode": "two_step",
            "action": action,
            "candidate_count": len(rows),
            "valid_count": len(valid),
            "selected_id": selected.bundle_id,
            "selected_bundle_valid": False,
            "failure": "MUTATION_COMPILATION_FAILED",
            "error": str(exc),
            "candidates": rows,
            "model_calls": [_usage(generator.last_result)],
            "jev_latency_ms": scorer.last_latency_ms,
            "jev_usage": scorer.last_usage,
        }
    fidelity_valid, fidelity_reason = validate_compilation_fidelity(selected, bundle)
    mutation_valid, mutation_reason = validate_candidate_bundle(world, bundle)
    return {
        "mode": "two_step",
        "action": action,
        "candidate_count": len(rows),
        "valid_count": len(valid),
        "selected_id": selected.bundle_id,
        "fidelity_valid": fidelity_valid,
        "fidelity_rejection": fidelity_reason,
        "selected_bundle_valid": mutation_valid,
        "mutation_rejection": mutation_reason,
        "compiled_mutation_count": len(bundle.mutations),
        "spoken_opening": has_spoken_opening(bundle),
        "counterattack": has_counterattack(bundle),
        "candidates": rows,
        "compiled_bundle": bundle.model_dump(mode="json"),
        "model_calls": [_usage(generator.last_result), _usage(compiler.last_result)],
        "jev_latency_ms": scorer.last_latency_ms,
        "jev_usage": scorer.last_usage,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=1, help="Repeats per action (billable).")
    parser.add_argument("--candidate-count", type=int, default=load_candidate_count())
    parser.add_argument("--mode", choices=("one_step", "two_step", "both"), default="both")
    parser.add_argument("--output", help="Optional path for the JSON report.")
    args = parser.parse_args()
    if args.runs < 1 or args.candidate_count < 1:
        parser.error("--runs and --candidate-count must both be positive")

    settings = get_settings()
    if not settings.openrouter_api_key or not settings.model_adjudicator:
        raise SystemExit("OPENROUTER_API_KEY and MODEL_ADJUDICATOR must be configured")

    world, model_world = mock_world()
    client = OpenRouterModelClient(settings)
    one_step_generator = ModelCandidateGenerator(client)
    outcome_generator = ModelOutcomeGenerator(client)
    compiler = ModelMutationCompiler(client)
    scorer = OpenRouterJevScorer(settings)
    reports = []
    for run in range(1, args.runs + 1):
        for action, action_class in (
            ("talk to the keeper", ActionClass.SOCIAL),
            ("attack the goblin", ActionClass.COMBAT),
        ):
            if args.mode in {"one_step", "both"}:
                report = run_one_step(
                    action,
                    world,
                    model_world,
                    one_step_generator,
                    scorer,
                    args.candidate_count,
                )
                report["run"] = run
                reports.append(report)
            if args.mode in {"two_step", "both"}:
                report = run_two_step(
                    action,
                    action_class,
                    world,
                    model_world,
                    outcome_generator,
                    compiler,
                    scorer,
                    args.candidate_count,
                )
                report["run"] = run
                reports.append(report)

    payload = json.dumps({"reports": reports}, indent=2)
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
