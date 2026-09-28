"""Multi-candidate action interpretation generator (TDD §8, §13, §14).

Prompts the generator LLM to produce N candidate interpretations for player intent,
each bundled with atomic state mutation primitives and draft narration.
"""

import json
from collections.abc import Sequence
from typing import Annotated, Any, Literal, Mapping, Protocol, TypeAlias, Union
from pydantic import BaseModel, ConfigDict, Field
import yaml

from app.domain.mutations import (
    AppendEvent,
    MoveEntity,
    MutateAttribute,
    MutationBundle,
    TransferEntity,
)
from app.domain.types import DomainModel, EventType, Role
from app.harness.model_client import ModelClient, StructuredResult


class CandidateGenerationResult(DomainModel):
    candidates: list[MutationBundle] = Field(
        min_length=1,
        description="Ranked candidate outcome bundles for the player's action.",
    )


class DialogueEventPayload(DomainModel):
    npc_id: str
    utterance: str | None
    dialogue_intent: str
    npc_reaction: str


class CombatEventPayload(DomainModel):
    target_id: str
    outcome: str
    hp_delta: int | None


class GeneralEventPayload(DomainModel):
    target_id: str | None
    item_id: str | None
    detail: str | None


class ModelAppendEvent(DomainModel):
    kind: Literal["APPEND_EVENT"] = "APPEND_EVENT"
    event_type: EventType
    payload: DialogueEventPayload | CombatEventPayload | GeneralEventPayload
    summary: str


ModelStateMutation: TypeAlias = Annotated[
    Union[MutateAttribute, TransferEntity, MoveEntity, ModelAppendEvent],
    Field(discriminator="kind"),
]


class ModelMutationBundle(DomainModel):
    """The model-visible subset of a mutation bundle.

    Execution metadata and mutation origin are application-owned and are
    deliberately absent from this schema, rather than exposed as nullable
    fields that a strict-output provider requires the model to populate.
    """

    bundle_id: str
    action_description: str
    rationale: str
    draft_narration: str
    mutations: list[ModelStateMutation] = Field(default_factory=list)


class ModelCandidateGenerationResult(DomainModel):
    candidates: list[ModelMutationBundle] = Field(
        min_length=1,
        description="Ranked candidate outcome bundles for the player's action.",
    )


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_json(item) for item in value]
    return value


def load_candidate_count(config_path: str = "config/runtime_rules.yaml") -> int:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return int(data.get("candidate_generation", {}).get("candidate_count", 3))
    except Exception:
        return 3


def load_candidate_max_output_tokens(
    config_path: str = "config/runtime_rules.yaml",
) -> int:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return int(
                data.get("candidate_generation", {}).get("max_output_tokens", 1800)
            )
    except Exception:
        return 1800


def load_candidate_validation_retries(
    config_path: str = "config/runtime_rules.yaml",
) -> int:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return max(
                0,
                int(
                    data.get("candidate_generation", {}).get(
                        "all_rejected_retries", 1
                    )
                ),
            )
    except Exception:
        return 1


GENERATOR_SYSTEM_PROMPT = """You are the action-neutral World Engine generator for a turn-based persistent world.
Your task is to interpret the player's action and generate {candidate_count} distinct possible outcome candidates.
If `validation_feedback` is non-empty, this is a bounded regeneration attempt:
correct every listed deterministic validation failure and do not repeat it.

For each candidate, provide:
1. `bundle_id`: Unique identifier (e.g. "cand_1", "cand_2")
2. `action_description`: Brief summary of the specific mechanical intent
3. `rationale`: At most 12 words explaining why the outcome is plausible
4. `draft_narration`: One short sentence describing the turn from the player's perspective
5. `mutations`: List of atomic state mutations. Allowed mutation kinds:
   - `MUTATE_ATTRIBUTE`: {{kind: "MUTATE_ATTRIBUTE", target_id: "<id>", path: "<path>", value: <val>, op: "SET" | "ADD" | "REMOVE"}}
     - Allowed numeric paths with SET/ADD: stats.hp, stats.mp, stats.attack, stats.defense, stats.speed, stats.dodge_pct, stats.skill, stats.xp, stats.level
     - Allowed condition paths with ADD/REMOVE only:
       * physical_conditions: BLEEDING, POISONED, BLINDED, STUNNED, CRIPPLED, BURNING, EXHAUSTED
       * mental_conditions: CHARMED, FRIGHTENED, CONFUSED, ENRAGED, TERRIFIED
     - Other allowed paths: status, disposition, light_state
   - `TRANSFER_ENTITY`: {{kind: "TRANSFER_ENTITY", entity_id: "<id>", from_ref: "<id>", to_ref: "<id>", location_kind: "CELL" | "INVENTORY" | "EQUIPPED" | "CONTAINER" | "NONE"}}
   - `MOVE_ENTITY`: {{kind: "MOVE_ENTITY", entity_id: "<id>", target_cell_id: "<id>"}}
   - `APPEND_EVENT`: use the typed payload matching the interaction:
     * Dialogue: {{npc_id, utterance, dialogue_intent, npc_reaction}}
     * Combat: {{target_id, outcome, hp_delta}}
     * Other events: {{target_id, item_id, detail}}

CRITICAL INVARIANTS:
- Only reference entity and cell IDs that explicitly exist in the provided room state.
- Entity distinctions:
  * NPC: Can engage in dialogue, trust progression, quests, and trade.
  * ENEMY / BOSS: Hostile. Dialogue or verbal actions may be attempted for narrative/reaction flavor, but enemies NEVER form friendly bonds, gain player trust, or offer friendly NPC quests/facts.
- Match outcome diversity to the action class, rather than defaulting to combat:
  * Dialogue/social: acknowledgement, substantive response, guarded disclosure,
    refusal, evasion, misunderstanding, negotiation, or disposition-shaped reaction.
  * Combat: hit, miss, defense, partial effect, counterattack, or condition.
  * Exploration: discovery, incomplete observation, environmental response, or danger.
  * Inventory/use: successful use, unsuitable use, consumption, transfer, or side effect.
- For dialogue, `npc_id` must be the addressed visible character ID. Preserve explicit
  player words in `utterance`; otherwise use null and describe the conversational goal
  in `dialogue_intent` (for example `INITIATE_CONVERSATION`). `npc_reaction` describes
  the proposed response mode, not invented canonical knowledge. For
  `INITIATE_CONVERSATION`, propose a spoken opening such as `SPOKEN_GREETING`,
  `GUARDED_QUESTION`, or `VERBAL_REFUSAL`; a nod, stare, or silence alone is not a
  conversation. Do not invent facts.
- Do not invent a physical mutation merely to make a dialogue bundle non-empty.
- Conditions: Add or remove individual condition tags using ADD/REMOVE ops. Never attempt to replace condition lists wholesale with SET.
- For combat damage, set the combat payload's `hp_delta` to a negative integer. Use
  null for a miss, dodge, parry, or other outcome with no HP change. Do not also emit
  a `stats.hp` mutation; application code derives it from `hp_delta`.
- Range diverse outcomes across candidates: e.g. direct success, partial/glancing effect, enemy reaction/counter, or environmental interaction.
- Keep every string terse. The {candidate_count} candidates should vary in outcome, not repeat the same outcome with verbose wording.
- Return ONLY a valid JSON object matching the CandidateGenerationResult schema.
"""


class CandidateGenerator(Protocol):
    def generate_candidates(
        self,
        player_input: str,
        world_snapshot: Mapping[str, Any],
        candidate_count: int | None = None,
        validation_feedback: list[str] | None = None,
    ) -> CandidateGenerationResult: ...


class FakeCandidateGenerator:
    """Deterministic generator for testing and offline runs."""

    def __init__(self, predefined_results: list[CandidateGenerationResult] | None = None):
        self._predefined = list(predefined_results or [])
        self._calls: list[dict[str, Any]] = []

    def generate_candidates(
        self,
        player_input: str,
        world_snapshot: Mapping[str, Any],
        candidate_count: int | None = None,
        validation_feedback: list[str] | None = None,
    ) -> CandidateGenerationResult:
        self._calls.append(
            {
                "input": player_input,
                "world": world_snapshot,
                "count": candidate_count,
                "validation_feedback": validation_feedback,
            }
        )
        if self._predefined:
            return self._predefined.pop(0)

        count = candidate_count or 3
        player_id = world_snapshot.get("player", {}).get("id", "player_1")
        return CandidateGenerationResult(
            candidates=[
                MutationBundle(
                    bundle_id=f"cand_{i+1}",
                    action_description=f"Action candidate {i+1} for '{player_input}'",
                    rationale="Generated fallback candidate",
                    draft_narration=f"You attempt to {player_input}.",
                    mutations=[],
                )
                for i in range(count)
            ]
        )


class ModelCandidateGenerator:
    """Generate candidate bundles with the configured OpenRouter chat model."""

    def __init__(self, client: ModelClient) -> None:
        self.client = client
        self.last_result: StructuredResult[ModelCandidateGenerationResult] | None = None

    def generate_candidates(
        self,
        player_input: str,
        world_snapshot: Mapping[str, Any],
        candidate_count: int | None = None,
        validation_feedback: list[str] | None = None,
    ) -> CandidateGenerationResult:
        count = candidate_count or 3
        system = GENERATOR_SYSTEM_PROMPT.format(candidate_count=count)
        user = json.dumps(
            {
                "player_input": player_input,
                "world_snapshot": _plain_json(world_snapshot),
                "candidate_count": count,
                "validation_feedback": validation_feedback or [],
            },
            separators=(",", ":"),
            default=str,
        )
        self.last_result = self.client.structured(
            Role.ADJUDICATOR,
            system,
            user,
            ModelCandidateGenerationResult,
            temperature=0.7,
            max_output_tokens=load_candidate_max_output_tokens(),
            timeout_s=20.0,
        )
        def canonical_mutations(candidate: ModelMutationBundle) -> list[Any]:
            combat_targets = {
                mutation.payload.target_id
                for mutation in candidate.mutations
                if isinstance(mutation, ModelAppendEvent)
                and isinstance(mutation.payload, CombatEventPayload)
                and mutation.payload.hp_delta is not None
            }
            converted: list[Any] = []
            for mutation in candidate.mutations:
                if (
                    isinstance(mutation, MutateAttribute)
                    and mutation.path == "stats.hp"
                    and mutation.target_id in combat_targets
                ):
                    # The typed combat payload is the single source of truth.
                    continue
                if not isinstance(mutation, ModelAppendEvent):
                    converted.append(mutation)
                    continue
                payload = mutation.payload.model_dump(exclude_none=True)
                if isinstance(mutation.payload, CombatEventPayload):
                    delta = mutation.payload.hp_delta
                    payload.pop("hp_delta", None)
                    if delta is not None:
                        normalized_delta = -abs(delta)
                        payload["damage"] = abs(normalized_delta)
                        converted.append(
                            AppendEvent(
                                event_type=mutation.event_type,
                                payload=payload,
                                summary=mutation.summary,
                            )
                        )
                        converted.append(
                            MutateAttribute(
                                target_id=mutation.payload.target_id,
                                path="stats.hp",
                                value=normalized_delta,
                                op="ADD",
                            )
                        )
                        continue
                converted.append(
                    AppendEvent(
                        event_type=mutation.event_type,
                        payload=payload,
                        summary=mutation.summary,
                    )
                )
            return converted

        return CandidateGenerationResult(
            candidates=[
                MutationBundle(
                    **{
                        **candidate.model_dump(exclude={"mutations"}),
                        "mutations": canonical_mutations(candidate),
                    }
                )
                for candidate in self.last_result.parsed.candidates
            ]
        )
