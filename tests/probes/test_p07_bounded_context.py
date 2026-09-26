"""P07 — bounded context under a large irrelevant history (TDD §16.2, §16.4).

The §16.4 script is the evidence artefact; this keeps its claim honest in CI.
It runs a small version (hundreds of events, not ten thousand) so the suite
stays fast, and asserts the property rather than a specific token count, which
would break every time a prompt is reworded.
"""

from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seed_stress_history.py"


def _script():
    if "seed_stress_history" in sys.modules:
        return sys.modules["seed_stress_history"]
    spec = importlib.util.spec_from_file_location("seed_stress_history", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["seed_stress_history"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _fresh():
    from app.services.stubs import reset_stubs

    reset_stubs()
    yield
    reset_stubs()


@pytest.fixture
def seeded():
    """A campaign carrying a few hundred irrelevant events and some memories."""
    script = _script()
    from app.services.stubs import get_engine

    engine = get_engine()
    campaign = engine.create_campaign("Probe", 7)
    rng = random.Random(7)
    embed, _ = script.make_embedder(8)

    engine.append_events(
        campaign.campaign_id,
        list(script.synthetic_events(campaign.campaign_id, 0, 600, rng)),
    )
    engine.append_memories(
        campaign.campaign_id,
        list(script.synthetic_memories(campaign.campaign_id, 0, 60, rng, embed)),
    )
    return script, engine, campaign.campaign_id


def test_the_engine_reports_the_history_it_stored(seeded):
    _, engine, campaign_id = seeded
    stats = engine.history_stats(campaign_id)
    assert stats["events"] == 600
    assert stats["memories"] == 60
    assert stats["stored_bytes"] > 100_000, "the history should be substantial"


def test_context_stays_bounded_as_history_grows(seeded):
    """The P07 property: manifest size independent of stored history."""
    script, engine, campaign_id = seeded
    measure = script._real_context_measurer()
    if measure is None:
        pytest.skip("Developer B's context_builder is not importable")

    small = measure(engine, campaign_id, samples=1)

    rng = random.Random(11)
    engine.append_events(
        campaign_id, list(script.synthetic_events(campaign_id, 600, 9_400, rng))
    )
    assert engine.history_stats(campaign_id)["events"] == 10_000

    large = measure(engine, campaign_id, samples=1)

    assert large["estimated_tokens"] <= script.DEFAULT_BUDGET_TOKENS
    # A ~17x increase in stored history must not meaningfully move the context.
    growth = large["estimated_tokens"] / small["estimated_tokens"]
    assert growth <= 1.10, (
        f"context grew {growth:.2f}x with history "
        f"({small['estimated_tokens']} -> {large['estimated_tokens']} tokens)"
    )


def test_the_recent_event_window_is_what_bounds_it(seeded):
    """Not a coincidence: the policy window caps how many events are included."""
    script, engine, campaign_id = seeded
    measure = script._real_context_measurer()
    if measure is None:
        pytest.skip("Developer B's context_builder is not importable")

    result = measure(engine, campaign_id, samples=1)
    assert 0 < result["event_ids"] <= 10, (
        "a bounded window should contribute a handful of events, not hundreds; "
        f"got {result['event_ids']}"
    )


def test_embedder_returns_one_flat_vector_per_text():
    """Regression: the batch embed API was called with a bare string.

    That made it iterate the characters and return one vector per character,
    inflating a single memory document to about 1.8 MB.
    """
    script = _script()
    embed, label = script.make_embedder(8)
    vector = embed("a memory about Mara")

    assert len(vector) == 8
    assert all(isinstance(v, float) for v in vector)
    assert "provider" not in label or "dims" in label


def test_stored_memory_documents_stay_small(seeded):
    import json

    _, engine, campaign_id = seeded
    memories = engine.raw_memories(campaign_id)
    biggest = max(len(json.dumps(m)) for m in memories)
    assert biggest < 5_000, f"a memory document ballooned to {biggest} bytes"
