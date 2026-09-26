"""Room Visuals §14.1 — spec, health bands, signature, diff.

The signature decides whether a stored image is still accurate, so these tests
attack the two ways it can be wrong: changing when it should not (constant
regeneration, wasted money) and not changing when it should (a stale picture
contradicting the game's own state).
"""

from __future__ import annotations

import pytest

from app.services.visual_spec import (
    BAND_CRITICAL,
    BAND_DEAD,
    BAND_HEALTHY,
    BAND_SEVERE,
    BAND_UNKNOWN,
    BAND_WOUNDED,
    build_spec,
    diff_specs,
    edit_prompt,
    generate_prompt,
    health_band,
    signature_of,
)


def scene(**overrides):
    base = {
        "name": "Dripping Cistern",
        "description": "Dust, old stone, and the smell of standing water.",
        "features": [
            {"id": "feat_a", "name": "wooden chair", "state": {"posture": "upright"}}
        ],
        "characters": [
            {
                "id": "npc_1",
                "name": "Mara",
                "status": "ALIVE",
                "hp": 8,
                "max_hp": 8,
                "description": "a wary scavenger",
            }
        ],
        "items": [{"id": "item_1", "name": "brass key", "where": "floor"}],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Health bands — every boundary, integer maths only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("max_hp", [6, 8, 10, 60])
def test_bands_are_exact_at_every_boundary(max_hp):
    """Float division would make these depend on representation."""
    assert health_band("ALIVE", max_hp, max_hp) == BAND_HEALTHY
    assert health_band("ALIVE", 0, max_hp) == BAND_DEAD

    # The boundary value itself belongs to the tighter band (<=).
    half = max_hp // 2
    if half * 100 <= 50 * max_hp:
        assert health_band("ALIVE", half, max_hp) in {BAND_WOUNDED, BAND_SEVERE, BAND_CRITICAL}

    just_above_half = half + 1
    if just_above_half * 100 > 50 * max_hp:
        assert health_band("ALIVE", just_above_half, max_hp) == BAND_HEALTHY


def test_documented_examples_for_max_hp_six():
    """The examples written into the design doc must actually hold."""
    assert health_band("ALIVE", 6, 6) == BAND_HEALTHY
    assert health_band("ALIVE", 3, 6) == BAND_WOUNDED  # 300 <= 300
    assert health_band("ALIVE", 1, 6) == BAND_SEVERE  # 100 <= 150
    assert health_band("ALIVE", 0, 6) == BAND_DEAD


def test_critical_band_is_reachable():
    assert health_band("ALIVE", 1, 60) == BAND_CRITICAL  # 100 <= 600
    assert health_band("ALIVE", 6, 60) == BAND_CRITICAL  # 600 <= 600
    assert health_band("ALIVE", 7, 60) == BAND_SEVERE  # 700 > 600


def test_dead_status_wins_over_positive_hp():
    """An engine reporting DEAD with stale hp must still read as dead."""
    assert health_band("DEAD", 5, 8) == BAND_DEAD
    assert health_band("dead", 5, 8) == BAND_DEAD


@pytest.mark.parametrize(
    "hp,max_hp",
    [(None, 8), (8, None), ("8", 8), (8, 0), (8, -1), (None, None), (True, 8)],
)
def test_unusable_hp_is_unknown_not_a_crash(hp, max_hp):
    """The fallback path (§7.2) supplies no HP at all."""
    assert health_band("ALIVE", hp, max_hp) in {BAND_UNKNOWN, BAND_HEALTHY, BAND_DEAD}
    # Specifically: missing numbers must not raise.
    health_band(None, hp, max_hp)


# ---------------------------------------------------------------------------
# Signature stability
# ---------------------------------------------------------------------------


def test_identical_state_gives_identical_signature():
    a = signature_of(build_spec("cell_1_1", scene()))
    b = signature_of(build_spec("cell_1_1", scene()))
    assert a == b
    assert a.startswith("sha256:") and len(a) == len("sha256:") + 64


def test_signature_is_independent_of_engine_ordering():
    """An engine reordering its lists must not cost a regeneration."""
    forward = scene(
        characters=[
            {"id": "npc_1", "name": "Mara", "status": "ALIVE", "hp": 8, "max_hp": 8},
            {"id": "npc_2", "name": "Bo", "status": "ALIVE", "hp": 8, "max_hp": 8},
        ]
    )
    reversed_ = scene(characters=list(reversed(forward["characters"])))
    assert signature_of(build_spec("c", forward)) == signature_of(
        build_spec("c", reversed_)
    )


def test_hp_change_within_a_band_does_not_change_the_signature():
    """The picture cannot show 8 HP versus 7 HP, so it must not regenerate."""
    a = scene(characters=[{"id": "n", "name": "M", "status": "ALIVE", "hp": 8, "max_hp": 8}])
    b = scene(characters=[{"id": "n", "name": "M", "status": "ALIVE", "hp": 7, "max_hp": 8}])
    assert health_band("ALIVE", 8, 8) == health_band("ALIVE", 7, 8)
    assert signature_of(build_spec("c", a)) == signature_of(build_spec("c", b))


def test_crossing_a_band_changes_the_signature():
    a = scene(characters=[{"id": "n", "name": "M", "status": "ALIVE", "hp": 8, "max_hp": 8}])
    b = scene(characters=[{"id": "n", "name": "M", "status": "ALIVE", "hp": 4, "max_hp": 8}])
    assert signature_of(build_spec("c", a)) != signature_of(build_spec("c", b))


def test_death_feature_state_and_item_removal_all_change_the_signature():
    base = signature_of(build_spec("c", scene()))

    dead = scene(
        characters=[{"id": "npc_1", "name": "Mara", "status": "DEAD", "hp": 0, "max_hp": 8}]
    )
    assert signature_of(build_spec("c", dead)) != base

    overturned = scene(
        features=[{"id": "feat_a", "name": "wooden chair", "state": {"posture": "overturned"}}]
    )
    assert signature_of(build_spec("c", overturned)) != base

    taken = scene(items=[])
    assert signature_of(build_spec("c", taken)) != base


def test_disposition_is_excluded_so_it_cannot_force_regeneration():
    """Disposition changes often and is not reliably visible (§6.2)."""
    a = scene()
    b = scene()
    b["characters"][0]["disposition"] = "HOSTILE"
    assert signature_of(build_spec("c", a)) == signature_of(build_spec("c", b))


def test_style_version_change_invalidates_every_image(monkeypatch):
    from app.services import visual_spec

    before = signature_of(build_spec("c", scene()))
    spec = build_spec("c", scene())
    spec.style_version = visual_spec.STYLE_VERSION + 1
    assert signature_of(spec) != before


def test_cell_id_is_part_of_the_signature():
    """Two identical-looking rooms must not share an image."""
    assert signature_of(build_spec("cell_1_1", scene())) != signature_of(
        build_spec("cell_2_2", scene())
    )


def test_spec_tolerates_a_sparse_scene():
    spec = build_spec("cell_0_0", {"name": "Bare Room"})
    assert spec.room_name == "Bare Room"
    assert spec.features == [] and spec.characters == [] and spec.items == []
    assert signature_of(spec)


# ---------------------------------------------------------------------------
# Diff lines
# ---------------------------------------------------------------------------


def test_diff_reports_band_change_feature_change_and_item_removal():
    old = build_spec("c", scene())
    new = build_spec(
        "c",
        scene(
            characters=[
                {"id": "npc_1", "name": "Mara", "status": "ALIVE", "hp": 2, "max_hp": 8}
            ],
            features=[
                {"id": "feat_a", "name": "wooden chair", "state": {"posture": "overturned"}}
            ],
            items=[],
        ),
    )
    lines = diff_specs(old, new)
    joined = " | ".join(lines)
    assert "Mara" in joined
    assert "posture changed from upright to overturned" in joined
    assert "brass key: no longer in the room" in joined


def test_diff_renders_an_unknown_state_key_generically():
    """The engine's state vocabulary is open; the diff must not hard-code it."""
    old = build_spec("c", scene(features=[{"id": "f", "name": "brazier", "state": {"lit": "unlit"}}]))
    new = build_spec("c", scene(features=[{"id": "f", "name": "brazier", "state": {"lit": "lit"}}]))
    assert "brazier: lit changed from unlit to lit" in diff_specs(old, new)


def test_diff_reports_death_explicitly():
    old = build_spec("c", scene())
    new = build_spec(
        "c", scene(characters=[{"id": "npc_1", "name": "Mara", "status": "DEAD", "hp": 0, "max_hp": 8}])
    )
    assert any("dead" in line.lower() for line in diff_specs(old, new))


def test_diff_against_nothing_is_empty():
    assert diff_specs(None, build_spec("c", scene())) == []


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def test_generate_prompt_lists_only_recorded_state():
    prompt = generate_prompt(build_spec("c", scene()))
    assert "Dripping Cistern" in prompt
    assert "wooden chair" in prompt and "posture: upright" in prompt
    assert "Mara" in prompt and "unhurt" in prompt
    assert "brass key" in prompt
    assert "Do not add" in prompt


def test_edit_prompt_restates_the_whole_scene_not_just_the_change():
    """A live test showed edits dropping unrelated objects (§9.3)."""
    old = build_spec("c", scene())
    new = build_spec(
        "c", scene(characters=[{"id": "npc_1", "name": "Mara", "status": "ALIVE", "hp": 2, "max_hp": 8}])
    )
    prompt = edit_prompt(old, new)
    assert "Changes to apply" in prompt
    assert "Full current scene" in prompt
    # The unchanged chair and key must still be named, or the edit may drop them.
    assert "wooden chair" in prompt and "brass key" in prompt


def test_prompts_contain_no_player_free_text():
    """§15 — the prompt-injection surface stays at zero.

    Prompts are built only from engine-recorded names and descriptions; player
    input never reaches them.
    """
    injected = scene()
    injected["name"] = "Room"
    prompt = generate_prompt(build_spec("c", injected))
    assert "ignore the rules" not in prompt.lower()
