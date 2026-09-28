"""The UI in a real browser (TDD §18, §22, §29.2).

What these cover that nothing else can: the event wiring, fetch, the in-flight
input lock, the minimap and inspector rendering against live data, and — the
one that matters most — that a script tag delivered through narration is
displayed as text and never executed.
"""

from __future__ import annotations

import json
import re
import time

import pytest

pytestmark = pytest.mark.e2e


def _new_campaign(page, live_server: str, name: str = "Ada") -> str:
    page.goto(live_server)
    page.wait_for_selector("#create-btn")
    page.fill("#new-name", name)
    page.click("#create-btn")
    # The campaign id lands in the header once creation completes...
    page.wait_for_function(
        "() => document.getElementById('meta-id').textContent.startsWith('cmp_')",
        timeout=15000,
    )
    # ...but the side panels are filled by a *later* async refresh, so wait for
    # the minimap too. Without this the panel assertions race the fetch and
    # fail only when the suite runs as a whole.
    page.wait_for_function(
        "() => document.querySelectorAll('#minimap .tile').length > 0",
        timeout=15000,
    )
    page.wait_for_function(
        "() => document.getElementById('character').textContent.includes('level')",
        timeout=15000,
    )
    return page.text_content("#meta-id").strip()


def _an_exit(page) -> str:
    """A direction that really is an exit here.

    Campaigns created through the UI have a random seed, so hardcoding
    "north" makes the test fail against a wall roughly half the time.
    """
    room = page.text_content("#room").lower()
    exits = [d for d in ("north", "south", "east", "west") if d in room]
    assert exits, f"the room panel listed no exits: {room!r}"
    return exits[0]


def _act(page, command: str) -> None:
    """Submit one turn and wait for the input to be re-enabled."""
    page.fill("#input-box", command)
    page.click("#submit-btn")
    page.wait_for_function(
        "() => !document.getElementById('input-box').disabled", timeout=15000
    )


def test_create_a_campaign_and_see_the_spawn_room(page, live_server):
    campaign_id = _new_campaign(page, live_server)

    assert campaign_id.startswith("cmp_")
    log = page.text_content("#narrative")
    assert "created" in log.lower()
    # The room panel is populated from the initial TurnResult.
    assert page.text_content("#room").strip()
    assert page.text_content("#meta-status").strip() == "ACTIVE"
    assert page.console_errors == [], page.console_errors


def test_walking_updates_the_log_room_and_minimap(page, live_server):
    _new_campaign(page, live_server)

    before_tiles = page.locator("#minimap .tile-known").count()
    room_before = page.text_content("#room")
    direction = _an_exit(page)

    _act(page, direction)

    log = page.text_content("#narrative")
    assert f"> {direction}" in log, "the player's command should appear in the log"
    assert page.text_content("#room") != room_before, "the room panel should change"

    page.wait_for_function(
        "count => document.querySelectorAll('#minimap .tile-known').length > count",
        arg=before_tiles,
        timeout=15000,
    )
    after_tiles = page.locator("#minimap .tile-known").count()
    assert after_tiles > before_tiles, "moving should discover a new cell"
    assert page.console_errors == [], page.console_errors


def test_the_minimap_renders_49_tiles_with_one_player_marker(page, live_server):
    _new_campaign(page, live_server)

    assert page.locator("#minimap .tile").count() == 49, "a 7x7 grid"
    assert page.locator("#minimap .tile-here").count() == 1
    assert page.text_content("#minimap .tile-here").strip() == "@"
    # Fog: almost everything is still unknown at spawn.
    assert page.locator("#minimap .tile-unknown").count() > 40


def test_walls_are_drawn_where_there_is_no_exit(page, live_server):
    _new_campaign(page, live_server)

    tile = page.locator("#minimap .tile-here")
    classes = tile.get_attribute("class")
    # The spawn cell of seed-less campaigns always has at least one wall.
    assert any(f"wall-{side}" in classes for side in ("top", "bottom", "left", "right"))


def test_character_panel_shows_starting_stats(page, live_server):
    _new_campaign(page, live_server, name="Bo")

    character = page.text_content("#character")
    assert "Bo" in character
    assert "20 / 20" in character, "HP meter"
    assert "6 / 6" in character, "MP meter"
    assert "ATK" in character and "DODGE" in character
    # Six carried slots, all empty at the start.
    assert page.locator("#character .inventory li").count() == 6


def test_input_is_disabled_while_a_turn_is_in_flight(page, live_server):
    """§18 — one turn at a time, enforced in the UI."""
    _new_campaign(page, live_server)

    pattern = re.compile(r".*/turns$")

    def slow_turn(route):
        # A plain sleep: calling back into the page from a route handler
        # (page.wait_for_timeout) deadlocks the sync Playwright dispatcher.
        time.sleep(0.8)
        route.continue_()

    page.route(pattern, slow_turn)
    try:
        page.fill("#input-box", "look")
        page.click("#submit-btn")

        page.wait_for_function(
            "() => document.getElementById('input-box').disabled", timeout=5000
        )
        assert page.is_disabled("#submit-btn"), "submit must be locked too"

        page.wait_for_function(
            "() => !document.getElementById('input-box').disabled", timeout=20000
        )
    finally:
        page.unroute(pattern, slow_turn)


def test_each_turn_sends_a_fresh_uuid_turn_id(page, live_server):
    """§7.1/§9.9 — the client generates the idempotency key."""
    _new_campaign(page, live_server)

    sent: list[str] = []
    page.on(
        "request",
        lambda request: sent.append(request.post_data or "")
        if request.url.endswith("/turns")
        else None,
    )

    _act(page, "look")
    _act(page, "wait")

    assert len(sent) >= 2
    ids = [json.loads(body)["turn_id"] for body in sent if body]
    uuid_v4 = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    for turn_id in ids:
        assert uuid_v4.match(turn_id), f"{turn_id} is not a v4 UUID"
    assert len(set(ids)) == len(ids), "each new turn needs a distinct turn_id"


def test_a_rejected_turn_is_shown_distinctly_and_changes_nothing(page, live_server):
    _new_campaign(page, live_server)

    # Find a direction the room panel does NOT list as an exit.
    room = page.text_content("#room").lower()
    blocked = next(
        d for d in ("north", "south", "east", "west") if d not in room
    )
    turn_before = page.text_content("#meta-turn")

    _act(page, blocked)

    assert page.locator(".log-rejected").count() >= 1, "rejections are styled apart"
    assert page.text_content("#meta-turn") == turn_before, "no turn was consumed"


def test_script_in_narration_is_displayed_not_executed(page, live_server):
    """§22 — the single most important thing this page must get right."""
    campaign_id = _new_campaign(page, live_server)

    payload = "<script>window.__pwned = true</script><img src=x onerror=\"window.__pwned=true\">"
    # Deliver the payload through the API, exactly as a model could.
    page.evaluate(
        """async ([id, text]) => {
            await fetch(`/api/campaigns/${id}/turns`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    turn_id: crypto.randomUUID(), player_id: 'player_1', input: text,
                }),
            });
        }""",
        [campaign_id, payload],
    )
    # Re-render the log through the normal path so the text reaches the page.
    _act(page, "look")

    assert page.evaluate("() => window.__pwned") is None, "injected script executed"
    assert page.locator("#narrative script").count() == 0
    assert page.locator("#narrative img").count() == 0
    assert page.dialogs == [], "no dialog should ever be raised"


def test_the_inspector_is_collapsed_but_present_and_opens_with_data(page, live_server):
    """§18 — collapsed by default; §9.7 — real manifest data when opened."""
    _new_campaign(page, live_server)
    _act(page, "I search the rubble for something useful")  # adjudicated path

    details = page.locator("#inspector-details")
    assert details.get_attribute("open") is None, "must start collapsed"

    details.locator("summary").click()
    page.wait_for_function(
        "() => document.getElementById('inspector').textContent.includes('policy')",
        timeout=15000,
    )

    inspector = page.text_content("#inspector")
    assert "ADJUDICATED" in inspector, "the path taken"
    assert "player_1" in inspector, "entity ids in context"
    assert "ADJUDICATOR" in inspector and "ms" in inspector, "model call latency"
    assert "v1" in inspector, "policy version"


def test_the_fast_path_says_no_model_was_asked(page, live_server):
    _new_campaign(page, live_server)
    _act(page, "look")

    page.locator("#inspector-details summary").click()
    page.wait_for_function(
        "() => document.getElementById('inspector').textContent.includes('status')",
        timeout=15000,
    )
    inspector = page.text_content("#inspector")
    assert "Placeholder" not in inspector, "stale placeholder copy left on screen"
    assert "FAST" in inspector
    assert "no context was built" in inspector


def test_reload_reconnects_to_the_same_campaign(page, live_server):
    """§29.2 — the page reconnects by resuming, not by replaying a transcript."""
    campaign_id = _new_campaign(page, live_server)
    _act(page, _an_exit(page))
    cell_before = page.text_content("#room")

    page.reload()
    page.wait_for_function(
        "() => document.getElementById('meta-id').textContent.startsWith('cmp_')",
        timeout=15000,
    )
    page.wait_for_function(
        "() => document.querySelectorAll('#minimap .tile-known').length >= 2",
        timeout=15000,
    )

    assert page.text_content("#meta-id").strip() == campaign_id
    assert "Resumed" in page.text_content("#narrative")
    assert page.text_content("#room") == cell_before, "the world is unchanged"
    assert page.console_errors == [], page.console_errors


def test_the_page_survives_a_server_restart(page, live_server):
    """The demo beat, from the browser's point of view.

    The server keeps running here; what is proven is that the page holds no
    world state of its own — a reload rebuilds everything from the store.
    """
    campaign_id = _new_campaign(page, live_server)
    _act(page, _an_exit(page))

    # Drop every trace of the session except the campaign id.
    page.evaluate("() => { sessionStorage.clear(); }")
    page.reload()
    page.wait_for_function(
        "() => document.getElementById('meta-id').textContent.startsWith('cmp_')",
        timeout=15000,
    )
    page.wait_for_function(
        "() => document.querySelectorAll('#minimap .tile-known').length >= 2",
        timeout=15000,
    )

    assert page.text_content("#meta-id").strip() == campaign_id
    assert page.locator("#minimap .tile-known").count() >= 2


def test_the_inspector_works_immediately_after_a_resume(page, live_server):
    """§29.2 — the demo opens the inspector right after resuming.

    Regression: `debug_available` reached the client only through a TurnResult,
    so after a reload the inspector stayed inert until another turn was taken.
    """
    _new_campaign(page, live_server)
    _act(page, "look")

    page.reload()
    page.wait_for_function(
        "() => document.getElementById('meta-id').textContent.startsWith('cmp_')",
        timeout=15000,
    )

    page.locator("#inspector-details summary").click()
    page.wait_for_function(
        "() => document.getElementById('inspector').textContent.includes('status')",
        timeout=10000,
    )
    inspector = page.text_content("#inspector")
    assert "No turn inspected yet" not in inspector
    assert "policy" in inspector, "the resume manifest should be inspectable"
