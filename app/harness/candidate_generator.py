"""Multi-candidate action interpretation generator (TDD §8, §13, §14).

Prompts the generator LLM to produce N candidate interpretations for player intent,
each bundled with atomic state mutation primitives and draft narration.
"""

from typing import Any, Mapping, Protocol
from pydantic import BaseModel, ConfigDict, Field
import yaml

from app.domain.mutations import MutationBundle
from app.domain.types import DomainModel


class CandidateGenerationResult(DomainModel):
    candidates: list[MutationBundle] = Field(
        min_length=1,
        description="Ranked candidate outcome bundles for the player's action.",
    )


def load_candidate_count(config_path: str = "config/runtime_rules.yaml") -> int:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return int(data.get("candidate_generation", {}).get("candidate_count", 3))
    except Exception:
        return 3


GENERATOR_SYSTEM_PROMPT = """You are the World Engine generator for a turn-based dungeon crawler.
Your task is to interpret the player's action and generate {candidate_count} distinct possible outcome candidates.

For each candidate, provide:
1. `bundle_id`: Unique identifier (e.g. "cand_1", "cand_2")
2. `action_description`: Brief summary of the specific mechanical intent
3. `rationale`: Why this candidate is plausible given the room state and rules
4. `draft_narration`: 1-3 sentences describing the turn from the player's perspective
5. `mutations`: List of atomic state mutations. Allowed mutation kinds:
   - `MUTATE_ATTRIBUTE`: {{kind: "MUTATE_ATTRIBUTE", target_id: "<id>", path: "<path>", value: <val>, op: "SET" | "ADD" | "REMOVE"}}
     - Allowed numeric paths with SET/ADD: stats.hp, stats.mp, stats.attack, stats.defense, stats.speed, stats.dodge_pct, stats.skill, stats.xp, stats.level
     - Allowed condition paths with ADD/REMOVE only:
       * physical_conditions: BLEEDING, POISONED, BLINDED, STUNNED, CRIPPLED, BURNING, EXHAUSTED
       * mental_conditions: CHARMED, FRIGHTENED, CONFUSED, ENRAGED, TERRIFIED
     - Other allowed paths: status, disposition, light_state
   - `TRANSFER_ENTITY`: {{kind: "TRANSFER_ENTITY", entity_id: "<id>", from_ref: "<id>", to_ref: "<id>", location_kind: "CELL" | "INVENTORY" | "EQUIPPED" | "CONTAINER" | "NONE"}}
   - `MOVE_ENTITY`: {{kind: "MOVE_ENTITY", entity_id: "<id>", target_cell_id: "<id>"}}
   - `APPEND_EVENT`: {{kind: "APPEND_EVENT", event_type: "<EVENT_TYPE>", payload: {{...}}, summary: "<summary>"}}

CRITICAL INVARIANTS:
- Only reference entity and cell IDs that explicitly exist in the provided room state.
- Entity distinctions:
  * NPC: Can engage in dialogue, trust progression, quests, and trade.
  * ENEMY / BOSS: Hostile. Dialogue or verbal actions may be attempted for narrative/reaction flavor, but enemies NEVER form friendly bonds, gain player trust, or offer friendly NPC quests/facts.
- Conditions: Add or remove individual condition tags using ADD/REMOVE ops. Never attempt to replace condition lists wholesale with SET.
- Range diverse outcomes across candidates: e.g. direct success, partial/glancing effect, enemy reaction/counter, or environmental interaction.
- Return ONLY a valid JSON object matching the CandidateGenerationResult schema.
"""


class CandidateGenerator(Protocol):
    def generate_candidates(
        self,
        player_input: str,
        world_snapshot: Mapping[str, Any],
        candidate_count: int | None = None,
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
    ) -> CandidateGenerationResult:
        self._calls.append(
            {
                "input": player_input,
                "world": world_snapshot,
                "count": candidate_count,
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
