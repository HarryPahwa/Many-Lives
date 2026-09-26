"""Read-only narration over committed events and a post-commit snapshot."""

from __future__ import annotations

import json

from app.domain.types import (
    CellSnapshot,
    Claim,
    ClaimAttribute,
    Event,
    ModelCallRecord,
    NarrationResult,
    PlayerSummary,
    Role,
    SocialContext,
)
from app.harness.model_client import ModelClient


_SYSTEM = """You narrate a grounded dark-fantasy dungeon in second-person present tense.
Describe only supplied events and the current snapshot. Narrate events in event_index
order: the player's action first, then any responses to it. Never invent an entity, exit,
outcome, number, or world fact. Every character, item, or feature mentioned in prose
must have at least a present claim. Historical memories are background; current state
wins. Never write an entity id. Name characters and items from the snapshot or from
attacker_name, defender_name, killer_names, and item_name on the events. When
social is supplied, the NPC answers the player's recent_dialogue with at
least one quoted line of speech, in character and shaped by disposition. NPC speech may
reveal only supplied allowed_facts or revealed_fact; for anything else (a personal name,
history, directions) the NPC deflects, evades, or answers vaguely instead of inventing
it. Keep scene description to one sentence on a dialogue turn. When
rejection_reason is set, the player's attempt changed nothing and rejection_reason is
already shown to them verbatim: do not restate it, never describe the attempted change
as happening, and write at most two short sentences of the unchanged scene. Keep prose
to 120 words or fewer. Return JSON only."""


def _model_call(result) -> ModelCallRecord:
    return ModelCallRecord(
        role=Role.NARRATOR,
        model=result.model,
        input_tokens=result.usage.get("input_tokens", 0),
        output_tokens=result.usage.get("output_tokens", 0),
        latency_ms=result.latency_ms,
        attempts=result.attempts,
        schema_valid=True,
    )


def _snapshot_payload(snapshot: CellSnapshot) -> dict[str, object]:
    return snapshot.model_dump(mode="json")


def _display_names(events: list[Event], snapshot: CellSnapshot) -> dict[str, str]:
    """Ids the player must not see, mapped to the name known when the event was written."""
    names: dict[str, str] = {}
    for entity in (*snapshot.features, *snapshot.characters, *snapshot.items):
        if entity.name and entity.name != entity.entity_id:
            names[entity.entity_id] = entity.name
    for event in events:
        payload = event.payload
        for id_key, name_key in (
            ("attacker_id", "attacker_name"),
            ("defender_id", "defender_name"),
            ("item_id", "item_name"),
        ):
            entity_id = payload.get(id_key)
            name = payload.get(name_key)
            if isinstance(entity_id, str) and isinstance(name, str) and name and name != entity_id:
                names[entity_id] = name
        killers = payload.get("killer_ids")
        killer_names = payload.get("killer_names")
        if isinstance(killers, list) and isinstance(killer_names, list):
            for entity_id, name in zip(killers, killer_names):
                if isinstance(entity_id, str) and isinstance(name, str) and name and name != entity_id:
                    names[entity_id] = name
    return names


def _use_names(prose: str, names: dict[str, str]) -> str:
    for entity_id, name in sorted(names.items(), key=lambda item: len(item[0]), reverse=True):
        prose = prose.replace(entity_id, name)
    return prose


def narrate(
    *,
    events: list[Event] | None,
    rejection_reason: str | None,
    snapshot: CellSnapshot,
    player_summary: PlayerSummary,
    social: SocialContext | None,
    context_text: str,
    client: ModelClient,
) -> tuple[NarrationResult, ModelCallRecord]:
    """Return description only; callers retain all state authority."""

    payload = {
        "events": [event.model_dump(mode="json") for event in events or []],
        "rejection_reason": rejection_reason,
        "snapshot": _snapshot_payload(snapshot),
        "player_summary": player_summary.model_dump(mode="json"),
        "social": social.model_dump(mode="json") if social else None,
        "context": context_text,
    }
    result = client.structured(
        Role.NARRATOR,
        _SYSTEM,
        json.dumps(payload, separators=(",", ":"), sort_keys=True),
        NarrationResult,
        temperature=0.7,
        max_output_tokens=500,
        timeout_s=25.0,
    )
    prose = _use_names(result.parsed.prose, _display_names(events or [], snapshot))
    parsed = result.parsed.model_copy(update={"prose": prose})
    if len(parsed.prose.split()) > 120:
        raise ValueError("Narration exceeds the 120-word limit")
    return parsed, _model_call(result)


def fallback_narration(events: list[Event], snapshot: CellSnapshot) -> NarrationResult:
    """Deterministic post-commit fallback that never needs a model."""

    if events:
        event = events[-1]
        templates = {
            "PLAYER_MOVED": f"You enter the {snapshot.name}.",
            "ATTACK_RESOLVED": "The clash leaves its mark in the room.",
            "ITEM_TRANSFERRED": "The exchange is complete.",
            "ENTITY_DIED": "A stillness settles over the fallen.",
            "ENTITY_REANIMATED": "The fallen stirs and stands, whole again.",
            "FEATURE_STATE_CHANGED": "Something in the room has changed.",
            "DISPOSITION_CHANGED": "The air between you shifts.",
            "DIALOGUE": "Your words hang in the dungeon air.",
        }
        prose = templates.get(event.type.value, f"Events unfold in the {snapshot.name}.")
    else:
        prose = f"You are in the {snapshot.name}."
    claims = [
        Claim(entity_id=entity.entity_id, attribute=ClaimAttribute.PRESENT, value=True)
        for entity in [*snapshot.features, *snapshot.characters, *snapshot.items]
    ]
    return NarrationResult(prose=prose, claims=claims)


def regenerate_with_contradictions(
    previous: NarrationResult,
    contradictions: list[str],
    **kwargs: object,
) -> tuple[NarrationResult, ModelCallRecord]:
    """Ask once for a corrected narration; orchestration controls retry count."""

    context_text = str(kwargs.pop("context_text", ""))
    kwargs["context_text"] = (
        f"{context_text}\n[contradictions_to_fix]\n"
        + "\n".join(contradictions)
        + f"\n[previous_narration]\n{previous.prose}"
    )
    return narrate(**kwargs)  # type: ignore[arg-type]
