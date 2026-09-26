"""Deterministic valid fallback room dressing (TDD §14.8)."""

from __future__ import annotations

from app.domain.rng import cell_plan_rng
from app.domain.types import (
    EntityDressing,
    FeatureDressing,
    FeatureProperty,
    ItemDressing,
    RoomDressing,
    StaticEnvironment,
)
from app.world.room_planner import PlannedRoom
from app.world.room_validation import validate_room_dressing


ROOM_NAMES = {
    "EMPTY": "Collapsed Passage",
    "ENEMY": "Guard Post",
    "NPC": "Dustbound Archive",
    "ITEM": "Forgotten Storehouse",
    "ENEMY_WITH_ITEM": "Broken Barracks",
    "NPC_WITH_ITEM": "Pilgrim's Refuge",
    "ENEMY_AND_NPC": "Contested Crossing",
    "BOSS": "The Iron Sanctum",
}


def build_fallback_dressing(planned: PlannedRoom, *, seed: int) -> RoomDressing:
    plan = planned.dressing_plan
    rng = cell_plan_rng(seed, plan.cell_key)
    features: list[FeatureDressing] = []
    used_names: set[str] = set()

    for slot in plan.item_slots:
        if slot.placement.value == "CONTAINER":
            features.append(
                FeatureDressing(
                    slot_id=slot.container_slot_id,
                    kind="crate",
                    name=f"iron-bound crate {len(features) + 1}",
                    properties=[FeatureProperty.CONTAINER, FeatureProperty.MOVABLE],
                    initial_state={"open_state": "closed", "lock_state": "unlocked"},
                )
            )
        elif slot.placement.value == "HIDDEN":
            features.append(
                FeatureDressing(
                    slot_id=slot.container_slot_id,
                    kind="rubble pile",
                    name=f"shadowed rubble pile {len(features) + 1}",
                    properties=[FeatureProperty.CONCEALING, FeatureProperty.HEAVY],
                    initial_state={"condition": "intact"},
                )
            )
    used_names.update(feature.name.casefold() for feature in features)

    generic = [
        ("torch sconce", "soot-blackened torch sconce", [FeatureProperty.LIGHT_SOURCE], {"light_state": "lit"}),
        ("stone bench", "weathered stone bench", [FeatureProperty.HEAVY], {"condition": "intact"}),
        ("wooden screen", "splintered wooden screen", [FeatureProperty.MOVABLE, FeatureProperty.BREAKABLE], {"condition": "intact"}),
    ]
    while len(features) < plan.feature_range[0]:
        kind, base_name, properties, state = generic[len(features) % len(generic)]
        name = base_name
        suffix = 2
        while name.casefold() in used_names:
            name = f"{base_name} {suffix}"
            suffix += 1
        used_names.add(name.casefold())
        features.append(
            FeatureDressing(
                slot_id=None,
                kind=kind,
                name=name,
                properties=properties,
                initial_state=state,
            )
        )

    entity_names = {
        "ENEMY": "tunnel goblin",
        "NPC": "dust-worn keeper",
        "BOSS": "bone warden",
    }
    entities = []
    for index, slot in enumerate(plan.entity_slots, start=1):
        base = entity_names[slot.role.value]
        entities.append(
            EntityDressing(
                slot_id=slot.slot_id,
                name=f"{base} {index}" if len(plan.entity_slots) > 1 else base,
                description=f"A {base} shaped by the dangers of tier {plan.tier}.",
                persona="Wary, terse, and attentive to old dungeon lore."
                if slot.role.value == "NPC"
                else None,
                traits=["weathered"],
            )
        )
    items = [
        ItemDressing(
            slot_id=slot.slot_id,
            name=f"{slot.subtype_hint.value.lower().replace('_', ' ')} {index}",
            description="A serviceable object marked by long years underground.",
        )
        for index, slot in enumerate(plan.item_slots, start=1)
    ]
    room_name = ROOM_NAMES[plan.archetype.value]
    # Consume a deterministic draw so future fallback table variants remain
    # independently reproducible without using global random state.
    lighting = rng.choice(["low amber light", "thin grey light"])
    dressing = RoomDressing(
        room_name=room_name,
        static_environment=StaticEnvironment(
            materials=["dark stone"],
            lighting=lighting,
            smell="cold dust and damp earth",
            architectural_notes="Low arches divide the worn chamber.",
        ),
        features=features,
        entities=entities,
        items=items,
    )
    return validate_room_dressing(planned, dressing)
