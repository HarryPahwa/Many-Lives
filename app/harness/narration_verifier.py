"""Deterministic checks of narration claims against post-commit state."""

from __future__ import annotations

import re

from app.domain.types import CellSnapshot, ClaimAttribute, NarrationResult, Verification


def _actual(entity, attribute: ClaimAttribute) -> str | bool | None:
    if attribute is ClaimAttribute.STATUS:
        return entity.status
    if attribute is ClaimAttribute.DISPOSITION:
        return entity.disposition
    if attribute is ClaimAttribute.LOCATION:
        return entity.location
    if attribute is ClaimAttribute.PRESENT:
        return True
    return entity.state.get(attribute.value)


def verify(
    result: NarrationResult, snapshot: CellSnapshot, campaign_entity_names: dict[str, str]
) -> Verification:
    """Count contradictions only; this function never changes state or prose."""

    entities = {
        entity.entity_id: entity
        for entity in [*snapshot.features, *snapshot.characters, *snapshot.items]
    }
    checked = contradictions = unknown = 0
    claimed_ids = {claim.entity_id for claim in result.claims}
    for claim in result.claims:
        entity = entities.get(claim.entity_id)
        if entity is None:
            unknown += 1
            continue
        checked += 1
        if _actual(entity, claim.attribute) != claim.value:
            contradictions += 1
    absent_mentions = 0
    visible_names = {entity.name.casefold() for entity in entities.values()}
    for entity_id, name in campaign_entity_names.items():
        if entity_id in claimed_ids or name.casefold() in visible_names:
            continue
        if re.search(rf"\b{re.escape(name)}\b", result.prose, flags=re.IGNORECASE):
            absent_mentions += 1
    return Verification(
        claims_checked=checked,
        contradictions=contradictions,
        unknown_entities=unknown,
        absent_entity_mentions=absent_mentions,
    )
