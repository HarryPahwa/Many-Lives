"""Visual scene spec, health bands, signature and diff (Room Visuals §6).

A spec is a *read-only projection of committed state*, exactly as narration is.
Nothing here reads or writes game data; it only reshapes a scene dict that the
engine already produced.

The signature is what decides whether a stored image is still accurate. It must
be stable across processes and insensitive to anything the picture cannot show,
or the feature will either regenerate constantly or show stale rooms.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: Bump to invalidate every stored image (a prompt or style change).
STYLE_VERSION = 1


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpecFeature(_Spec):
    id: str
    name: str
    #: Copied verbatim from the engine. Key names are deliberately not
    #: constrained: the stub uses `posture`/`lit`, the TDD's closed vocabulary
    #: uses others, and a spec that hard-coded either would silently ignore
    #: real changes.
    state: dict[str, str] = Field(default_factory=dict)


class SpecCharacter(_Spec):
    id: str
    name: str
    description: str = ""
    status: str
    band: str


class SpecItem(_Spec):
    id: str
    name: str
    where: str


class VisualSceneSpec(_Spec):
    """Everything the illustration is allowed to show."""

    style_version: int = STYLE_VERSION
    cell_id: str
    room_name: str
    description: str = ""
    features: list[SpecFeature] = Field(default_factory=list)
    characters: list[SpecCharacter] = Field(default_factory=list)
    items: list[SpecItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Health bands (§6.1) — integer arithmetic only, no float boundaries
# ---------------------------------------------------------------------------

BAND_DEAD = "DEAD"
BAND_CRITICAL = "CRITICAL"
BAND_SEVERE = "SEVERE"
BAND_WOUNDED = "WOUNDED"
BAND_HEALTHY = "HEALTHY"
BAND_UNKNOWN = "UNKNOWN"

#: Deliberately non-graphic: image services refuse gore, and a refusal is a
#: failed generation rather than a tamer picture.
BAND_PHRASES = {
    BAND_HEALTHY: "unhurt",
    BAND_WOUNDED: "bruised and scuffed, armour or clothing torn",
    BAND_SEVERE: "badly hurt, bloodied bandage-level wounds, stooped",
    BAND_CRITICAL: "barely standing or kneeling, exhausted",
    BAND_DEAD: "lying motionless on the floor",
    BAND_UNKNOWN: "",
}


def health_band(status: str | None, hp: Any, max_hp: Any) -> str:
    """Bucket a character's health for the picture.

    Uses `hp * 100 <= N * max_hp` rather than `hp / max_hp <= N / 100` so the
    boundaries are exact: with max_hp 6, hp 3 is WOUNDED because 300 <= 300,
    which float division would make dependent on representation.
    """
    if (status or "").upper() == BAND_DEAD:
        return BAND_DEAD
    # bool is a subclass of int, so `isinstance(True, int)` is True. A boolean
    # HP is nonsense from any engine and must read as unknown rather than
    # silently banding as 1 point of health.
    if isinstance(hp, bool) or isinstance(max_hp, bool):
        return BAND_UNKNOWN
    if not isinstance(hp, int) or not isinstance(max_hp, int) or max_hp <= 0:
        return BAND_UNKNOWN
    if hp <= 0:
        return BAND_DEAD
    if hp * 100 <= 10 * max_hp:
        return BAND_CRITICAL
    if hp * 100 <= 25 * max_hp:
        return BAND_SEVERE
    if hp * 100 <= 50 * max_hp:
        return BAND_WOUNDED
    return BAND_HEALTHY


# ---------------------------------------------------------------------------
# Building a spec
# ---------------------------------------------------------------------------


def _clean(value: Any) -> str:
    return "" if value is None else str(value)


def build_spec(cell_id: str, scene: dict[str, Any]) -> VisualSceneSpec:
    """Project an engine scene dict onto the spec.

    Lists are sorted by id so that an engine reordering its internals does not
    change the signature and trigger a needless regeneration.
    """
    features = sorted(
        (
            SpecFeature(
                id=_clean(f.get("id")),
                name=_clean(f.get("name")),
                state={
                    _clean(k): _clean(v) for k, v in dict(f.get("state") or {}).items()
                },
            )
            for f in scene.get("features") or []
        ),
        key=lambda f: f.id,
    )
    characters = sorted(
        (
            SpecCharacter(
                id=_clean(c.get("id")),
                name=_clean(c.get("name")),
                description=_clean(c.get("description")),
                status=_clean(c.get("status")),
                band=health_band(c.get("status"), c.get("hp"), c.get("max_hp")),
            )
            for c in scene.get("characters") or []
        ),
        key=lambda c: c.id,
    )
    items = sorted(
        (
            SpecItem(
                id=_clean(i.get("id")),
                name=_clean(i.get("name")),
                where=_clean(i.get("where")),
            )
            for i in scene.get("items") or []
        ),
        key=lambda i: i.id,
    )
    return VisualSceneSpec(
        cell_id=cell_id,
        room_name=_clean(scene.get("name")),
        description=_clean(scene.get("description")),
        features=features,
        characters=characters,
        items=items,
    )


def signature_of(spec: VisualSceneSpec) -> str:
    """Stable content hash of a spec (§6.3)."""
    payload = json.dumps(
        spec.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Diffing, for edit prompts (§6.4)
# ---------------------------------------------------------------------------


def diff_specs(old: VisualSceneSpec | None, new: VisualSceneSpec) -> list[str]:
    """Plain-language change lines, for an edit prompt."""
    if old is None:
        return []

    lines: list[str] = []

    if old.room_name != new.room_name:
        lines.append(f"the room is now called {new.room_name}")

    old_chars = {c.id: c for c in old.characters}
    new_chars = {c.id: c for c in new.characters}
    for cid, char in new_chars.items():
        previous = old_chars.get(cid)
        if previous is None:
            lines.append(f"{char.name}: has entered the room")
        elif previous.band != char.band:
            if char.band == BAND_DEAD:
                lines.append(f"{char.name}: is now dead, lying motionless on the floor")
            else:
                phrase = BAND_PHRASES.get(char.band, "")
                lines.append(
                    f"{char.name}: now {phrase}" if phrase else f"{char.name}: changed"
                )
    for cid, char in old_chars.items():
        if cid not in new_chars:
            lines.append(f"{char.name}: no longer in the room")

    old_feats = {f.id: f for f in old.features}
    new_feats = {f.id: f for f in new.features}
    for fid, feature in new_feats.items():
        previous = old_feats.get(fid)
        if previous is None:
            lines.append(f"{feature.name}: is now in the room")
            continue
        for key, value in feature.state.items():
            before = previous.state.get(key)
            if before != value:
                lines.append(
                    f"{feature.name}: {key} changed from {before} to {value}"
                )
        for key in previous.state:
            if key not in feature.state:
                lines.append(f"{feature.name}: {key} is no longer set")
    for fid, feature in old_feats.items():
        if fid not in new_feats:
            lines.append(f"{feature.name}: no longer in the room")

    old_items = {i.id: i for i in old.items}
    new_items = {i.id: i for i in new.items}
    for iid, item in new_items.items():
        if iid not in old_items:
            lines.append(f"{item.name}: is now in the room")
    for iid, item in old_items.items():
        if iid not in new_items:
            lines.append(f"{item.name}: no longer in the room")

    return lines


# ---------------------------------------------------------------------------
# Prompts (§9.2, §9.3)
# ---------------------------------------------------------------------------

_STYLE = (
    "Dark fantasy visual novel illustration. Wide establishing shot from the "
    "room entrance, eye level, consistent painterly style, no text, no "
    "captions, no UI."
)


def _scene_block(spec: VisualSceneSpec) -> str:
    parts = [f"Room: {spec.room_name}. {spec.description}".strip()]

    if spec.features:
        rendered = []
        for feature in spec.features:
            state = ", ".join(f"{k}: {v}" for k, v in sorted(feature.state.items()))
            rendered.append(f"{feature.name} ({state})" if state else feature.name)
        parts.append("Features: " + "; ".join(rendered))

    living = [c for c in spec.characters]
    if living:
        rendered = []
        for char in living:
            bits = [char.name]
            if char.description:
                bits.append(char.description)
            phrase = BAND_PHRASES.get(char.band, "")
            if phrase:
                bits.append(phrase)
            rendered.append(", ".join(bits))
        parts.append("Characters: " + "; ".join(rendered))

    if spec.items:
        parts.append(
            "Items visible in the room: " + ", ".join(i.name for i in spec.items)
        )

    parts.append(
        "Show only what is listed. Do not add characters, items, or furniture "
        "that are not listed."
    )
    return "\n".join(parts)


def generate_prompt(spec: VisualSceneSpec) -> str:
    return f"{_STYLE}\n{_scene_block(spec)}"


def edit_prompt(old: VisualSceneSpec | None, new: VisualSceneSpec) -> str:
    """Restate the whole scene, not only the change.

    A live test of the model showed an edit correctly applying the requested
    change while silently dropping unrelated objects, so every edit repeats the
    full scene (§9.3).
    """
    changes = diff_specs(old, new) or ["(no textual change; refresh the scene)"]
    return (
        "Edit the reference image. Keep the same camera angle, composition, "
        "lighting, style and architecture.\n"
        "Keep every element listed below exactly as it appears unless a change "
        "is listed.\n"
        "Changes to apply:\n"
        + "\n".join(f"- {line}" for line in changes)
        + "\nFull current scene (everything that must be visible):\n"
        + _scene_block(new)
        + "\nDo not add anything that is not listed."
    )
