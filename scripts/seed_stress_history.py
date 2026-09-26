"""Synthetic long-history script and the P07 probe (TDD §16.4, §16.2).

Builds a probe campaign with ~10,000 events and ~1,000 memories, then measures
the context the harness builds at increasing history sizes. Per §16.4 the
output — stored history size against the manifest token estimate — **is** the
bounded-context evidence for the Long Horizon claim.

What it does and does not show
------------------------------
It shows that stored history can grow by orders of magnitude while the context
assembled for one model call stays inside its budget. It is **not** evidence of
a billion-token live campaign, and the script says so in its own output (§16.4,
§3.2).

The measurement is only as strong as the context builder it drives. It uses
Developer B's real ``app.harness.context_builder`` when importable, so the
result is a measurement rather than a tautology; with ``--stub-context`` it
falls back to the in-repo stub, whose flat line is true *by construction*, and
the report labels it that way. An honest weak result beats a strong-looking one
that means nothing.

Growth is measured from the first non-empty checkpoint. Going from no history
to a few hundred events fills the policy's recent-event window once, and
counting that one-off rise as "growth with history" would describe the system
wrongly. Both figures are printed.

Usage
-----
    python scripts/seed_stress_history.py
    python scripts/seed_stress_history.py --events 10000 --memories 1000
    python scripts/seed_stress_history.py --out docs/p07_result.json

Runs in-process against the installed seam: 10,000 events over HTTP would be
slow and would measure the web layer rather than the context builder. Exits 0
when P07 passes, 1 when it fails, 3 when the installed engine cannot store
bulk history.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.stubs import (  # noqa: E402
    get_engine,
    get_harness,
    supports_history,
)

# §11.2 budget.max_context_tokens [DEF]. Overridable on the command line so a
# tuned policy can be measured without editing this file.
DEFAULT_BUDGET_TOKENS = 3000

# Checkpoints at which the context is rebuilt and measured.
DEFAULT_CHECKPOINTS = (0, 100, 1_000, 5_000, 10_000)

# §16.4: the sandbox may throttle around 100 ops/s, so history goes in batches.
BATCH_SIZE = 500

EMBEDDING_DIMS = 8  # only the storage cost matters here, not the geometry

_EVENT_TYPES = (
    "PLAYER_MOVED",
    "CELL_DISCOVERED",
    "ATTACK_RESOLVED",
    "ITEM_TRANSFERRED",
    "DIALOGUE",
    "FEATURE_STATE_CHANGED",
    "DISPOSITION_CHANGED",
)

_MEMORY_TEMPLATES = (
    "{who} was struck during a scuffle near {where}.",
    "{who} promised to open the {where} door if paid.",
    "A brass key changed hands in {where}.",
    "{who} refused to speak about the warden.",
    "The chair in {where} was overturned.",
)


def token_estimate(text: str) -> int:
    """§11.4: ceil(len(text) / 4)."""
    return math.ceil(len(text) / 4)


def synthetic_events(campaign_id: str, start: int, count: int, rng: random.Random):
    """Irrelevant-but-plausible history, spread over the 49 cells (§16.2 P07)."""
    for i in range(start, start + count):
        turn = i // 2
        x, y = rng.randrange(7), rng.randrange(7)
        yield {
            "event_id": f"evt_{turn}_{i % 2}",
            "campaign_id": campaign_id,
            "turn_sequence": turn,
            "event_index": i % 2,
            "turn_id": f"stress-{i}",
            "type": rng.choice(_EVENT_TYPES),
            "actor_id": rng.choice(["player_1", "npc_7f2a", "enemy_91c0"]),
            "entity_ids": ["player_1"],
            "cell_id": f"cell_{x}_{y}",
            "payload": {"n": i},
            "summary": f"Turn {turn}: routine activity in cell_{x}_{y}.",
            "memory_status": "NOT_REQUIRED",
            "schema_version": 1,
        }


def synthetic_memories(campaign_id: str, start: int, count: int, rng: random.Random,
                       embed):
    """Curated memory text plus an embedding (§12.3, §12.4)."""
    for i in range(start, start + count):
        who = rng.choice(["Mara", "the warden", "a hooded trader"])
        where = f"cell_{rng.randrange(7)}_{rng.randrange(7)}"
        text = rng.choice(_MEMORY_TEMPLATES).format(who=who, where=where)
        yield {
            "id": f"mem_stress_{i}",
            "campaign_id": campaign_id,
            "memory_type": rng.choice(["RELATIONSHIP", "EVENT", "PROMISE"]),
            "text": text,
            "entity_ids": ["npc_7f2a"],
            "cell_id": where,
            "embedding": embed(text),
            "schema_version": 1,
        }


def make_embedder(dims: int):
    """B's embedder when a real provider is configured; a stand-in otherwise.

    Returns ``(embed_fn, label)`` where ``embed_fn`` takes one string and
    returns one vector.

    Two things this has to get right, both learned by getting them wrong:

    * B's contract is a **batch** API — ``embed(texts: list[str]) ->
      list[list[float]]``. Passing a bare string makes it iterate the
      characters and return one vector per character, which inflated a single
      memory document to 1.8 MB.
    * A ``FakeModelClient`` is not a provider. Labelling it one would put a
      false statement in the evidence file, so the label names what actually
      ran.
    """

    def deterministic(text: str) -> list[float]:
        rng = random.Random(text)
        return [round(rng.uniform(-1.0, 1.0), 6) for _ in range(dims)]

    fallback_label = f"deterministic stand-in, {dims} dims"

    try:
        from app.config import get_settings
        from app.harness.model_client import get_model_client  # type: ignore

        settings = get_settings()
        if settings.use_fake_models or not settings.embedding_model:
            return deterministic, f"{fallback_label} (no provider configured)"

        client = get_model_client()
        if client is None or not hasattr(client, "embed"):
            return deterministic, f"{fallback_label} (no embed on client)"

        def provider(text: str) -> list[float]:
            vectors = client.embed([text])  # batch API: one text in, one out
            return list(vectors[0])

        probe = provider("dimension probe")
        if not probe or not all(isinstance(v, (int, float)) for v in probe):
            return deterministic, f"{fallback_label} (provider returned no flat vector)"
        return provider, f"provider {settings.embedding_model}, {len(probe)} dims"
    except Exception as exc:  # noqa: BLE001 - unconfigured is the normal case
        return deterministic, f"{fallback_label} ({type(exc).__name__})"


class _StoredHistoryView:
    """Read model over the seeded history, for Developer B's context builder.

    `recent_events` deliberately scans the **whole** stored log and then takes
    the tail, exactly as a campaign-scoped query with a sort and a `.limit()`
    would. That is what makes the measurement honest: the cost of having
    10,000 events is paid on the read, and what still has to stay flat is the
    context the builder assembles from it.
    """

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    def current_state(self, *, campaign, player, room, target_ids):
        return {
            "player_state": player,
            "current_cell": room,
            "visible_entities": [],
        }

    def recent_events(self, *, campaign_id, player_id, room_id, target_ids, limit):
        if not limit:
            return []
        scoped = [e for e in self._events if e.get("campaign_id") == campaign_id]
        scoped.sort(key=lambda e: (e.get("turn_sequence", 0), e.get("event_index", 0)))
        # Only the rows the query actually returns are mapped into domain
        # objects, which is what a repository does with a cursor.
        from app.domain.types import Event

        return [Event(**doc) for doc in scoped[-limit:]]


def _real_context_measurer():
    """Bind Developer B's build_context, or None when it is unavailable."""
    try:
        from app.harness.context_builder import build_context
        from app.harness.context_policy import CONTEXT_POLICY_V1, ActionClass
        from app.harness.model_client import Role
    except Exception:  # noqa: BLE001 - B's modules absent or changed
        return None

    def measure(engine, campaign_id: str, samples: int):
        camp_events = engine.raw_events(campaign_id)
        view = _StoredHistoryView(camp_events)
        world = engine.load_world_view(campaign_id, "player_1")
        campaign = {"campaign_id": campaign_id, "current_turn": world.current_turn}
        player = {
            "entity_id": world.player_id,
            "hp": world.player.hp,
            "max_hp": world.player.max_hp,
            "cell_id": world.player.cell_id,
        }
        room = {
            "cell_id": world.visible_cell.cell_id,
            "name": world.visible_cell.name,
            "exits": list(world.visible_cell.exits),
        }

        tokens: list[int] = []
        latencies: list[float] = []
        manifest = None
        text = ""
        for _ in range(samples):
            started = time.perf_counter()
            text, manifest = build_context(
                role=Role.ADJUDICATOR,
                action_class=ActionClass.SOCIAL,
                policy=CONTEXT_POLICY_V1,
                view=view,
                campaign=campaign,
                player=player,
                room=room,
                action_text="I ask Mara about the brass key.",
                target_ids=(),
                retriever=None,
            )
            latencies.append((time.perf_counter() - started) * 1000)
            tokens.append(int(getattr(manifest, "estimated_tokens", 0)))

        return {
            "estimated_tokens": int(statistics.median(tokens)),
            "measured_text_tokens": token_estimate(text),
            "components": len(getattr(manifest, "components", []) or []),
            "entity_ids": len(getattr(manifest, "entity_ids", []) or []),
            "event_ids": len(getattr(manifest, "event_ids", []) or []),
            "manifest_memories": len(getattr(manifest, "memories", []) or []),
            "build_ms_median": round(statistics.median(latencies), 3),
        }

    return measure


def measure_context(engine, harness, campaign_id: str, samples: int = 5):
    """Build a SOCIAL context and report its size and cost."""
    view = engine.load_world_view(campaign_id, "player_1")
    tokens: list[int] = []
    latencies: list[float] = []
    manifest = None
    text = ""
    for _ in range(samples):
        start = time.perf_counter()
        text, manifest = harness.build_context(view, "SOCIAL")
        latencies.append((time.perf_counter() - start) * 1000)
        tokens.append(int(manifest.estimated_tokens))

    return {
        "estimated_tokens": int(statistics.median(tokens)),
        "measured_text_tokens": token_estimate(text),
        "components": len(manifest.components),
        "entity_ids": len(manifest.entity_ids),
        "event_ids": len(manifest.event_ids),
        "manifest_memories": len(manifest.memories),
        "build_ms_median": round(statistics.median(latencies), 3),
    }


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}B" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def run(args: argparse.Namespace) -> int:
    engine, harness = get_engine(), get_harness()

    if not supports_history(engine):
        print(
            "The installed engine cannot store bulk history "
            f"({type(engine).__name__} has no append_events/append_memories).\n"
            "This measurement needs an engine implementing the optional "
            "HistoryStore capability in app/services/stubs.py.",
            file=sys.stderr,
        )
        return 3

    rng = random.Random(args.seed)
    embed, embed_label = make_embedder(args.dims)

    # Prefer Developer B's real context builder: with the stub the flat line
    # is a tautology, with B's builder it is a measurement.
    real_measure = None if args.stub_context else _real_context_measurer()
    if real_measure is not None and not hasattr(engine, "raw_events"):
        real_measure = None

    summary = engine.create_campaign("Probe", args.seed)
    campaign_id = summary.campaign_id
    print(f"Probe campaign : {campaign_id}  (seed {args.seed})")
    print(f"Engine         : {type(engine).__name__}")
    context_source = (
        "app.harness.context_builder (real)"
        if real_measure is not None
        else f"{type(harness).__name__} (stub)"
    )
    print(f"Harness        : {type(harness).__name__}")
    print(f"Context builder: {context_source}")
    print(f"Embeddings     : {embed_label}")
    print(f"Budget         : {args.budget} tokens (§11.2 max_context_tokens)\n")

    checkpoints = sorted({c for c in args.checkpoints if c <= args.events})
    if args.events not in checkpoints:
        checkpoints.append(args.events)

    rows: list[dict[str, Any]] = []
    events_written = 0
    memories_written = 0
    seed_seconds = 0.0

    for target in checkpoints:
        # Grow the stored history up to this checkpoint, in batches (§16.4).
        started = time.perf_counter()
        while events_written < target:
            batch = min(BATCH_SIZE, target - events_written)
            engine.append_events(
                campaign_id,
                list(synthetic_events(campaign_id, events_written, batch, rng)),
            )
            events_written += batch

        memory_target = (
            round(args.memories * target / args.events) if args.events else 0
        )
        while memories_written < memory_target:
            batch = min(BATCH_SIZE, memory_target - memories_written)
            engine.append_memories(
                campaign_id,
                list(
                    synthetic_memories(
                        campaign_id, memories_written, batch, rng, embed
                    )
                ),
            )
            memories_written += batch
        seed_seconds += time.perf_counter() - started

        stats = engine.history_stats(campaign_id)
        if real_measure is not None:  # noqa: SIM108
            context = real_measure(engine, campaign_id, args.samples)
        else:
            context = measure_context(engine, harness, campaign_id, args.samples)
        clash = set(stats) & set(context)
        assert not clash, f"stat/context key collision would hide data: {clash}"
        rows.append({**stats, **context})

        print(
            f"  events {stats['events']:>6,} | memories {stats['memories']:>5,} "
            f"| stored {human_bytes(stats['stored_bytes']):>8} "
            f"| context {context['estimated_tokens']:>5} tok "
            f"| build {context['build_ms_median']:>6.2f} ms"
        )

    # ---- P07 ----------------------------------------------------------
    #
    # The baseline is the first checkpoint with a non-empty history, not the
    # empty one. Going from 0 events to a few hundred fills the policy's
    # recent-event window for the first time, so context legitimately rises
    # once and then stops. Measuring from zero would report that one-off fill
    # as "growth with history" and describe the system wrongly. What P07
    # actually asks is whether context is independent of history *once the
    # window is saturated*, so that is what is measured; both numbers are
    # printed so the reader can check the choice.
    last = rows[-1]
    saturated = next((r for r in rows if r["events"] > 0), rows[0])
    baseline_tokens = saturated["estimated_tokens"] or 1

    history_growth = (
        last["events"] / saturated["events"] if saturated["events"] else 0.0
    )
    token_growth = last["estimated_tokens"] / baseline_tokens
    peak_tokens = max(r["estimated_tokens"] for r in rows)

    within_budget = peak_tokens <= args.budget
    independent = token_growth <= args.tolerance

    if rows[0]["events"] == 0 and len(rows) > 1:
        print(
            f"\nWindow fill (excluded from the ratio): "
            f"{rows[0]['estimated_tokens']} -> {saturated['estimated_tokens']} tokens "
            f"as the first {saturated['events']:,} events arrive."
        )
    print(f"\nStored history then grew {history_growth:,.0f}x "
          f"({saturated['events']:,} -> {last['events']:,} events, "
          f"{human_bytes(last['stored_bytes'])} stored).")
    print(f"Context token estimate went {saturated['estimated_tokens']} -> "
          f"{last['estimated_tokens']} ({token_growth:.2f}x), peak {peak_tokens}.")

    stub_harness = real_measure is None
    if stub_harness:
        print(
            "\nNOTE: the in-repo stub context builder does not read stored "
            "history,\n      so the flat line above is true by construction, not "
            "measured.\n      Re-run once Developer B's context builder is behind "
            "get_harness()\n      for this to be a real measurement."
        )

    print("\nP07 — bounded context under 10k irrelevant events")
    print(f"  estimated tokens <= budget ...... {'PASS' if within_budget else 'FAIL'}"
          f"  ({peak_tokens} <= {args.budget})")
    print(f"  manifest independent of history . {'PASS' if independent else 'FAIL'}"
          f"  (growth {token_growth:.2f}x <= {args.tolerance}x)")
    print("\nThis shows stored history growing while per-call context stays "
          "bounded.\nIt is not a claim about a billion-token live campaign "
          "(TDD §16.4, §3.2).")

    payload = {
        "probe_id": "P07_BOUNDED_CONTEXT",
        "campaign_id": campaign_id,
        "seed": args.seed,
        "engine": type(engine).__name__,
        "harness": type(harness).__name__,
        "context_builder": context_source,
        "harness_reads_history": not stub_harness,
        "embeddings": embed_label,
        "budget_tokens": args.budget,
        "checkpoints": rows,
        "baseline_events": saturated["events"],
        "baseline_tokens": saturated["estimated_tokens"],
        "history_growth_factor": round(history_growth, 2),
        "token_growth_factor": round(token_growth, 4),
        "baseline_note": (
            "Growth is measured from the first non-empty checkpoint. The rise "
            "from an empty history is the recent-event window filling once, "
            "not growth with history."
        ),
        "peak_estimated_tokens": peak_tokens,
        "seed_seconds": round(seed_seconds, 2),
        "passed": bool(within_budget and independent),
        "caveat": (
            "Stored history grows while per-call context stays within budget. "
            "Not evidence of a billion-token live campaign (TDD §16.4, §3.2)."
        ),
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWrote {out}")

    return 0 if payload["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=10_000)
    parser.add_argument("--memories", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--dims", type=int, default=EMBEDDING_DIMS)
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET_TOKENS)
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1.10,
        help="max allowed context growth factor across the whole run",
    )
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument(
        "--stub-context",
        action="store_true",
        help="force the in-repo stub context builder instead of B's",
    )
    parser.add_argument(
        "--checkpoints",
        type=int,
        nargs="*",
        default=list(DEFAULT_CHECKPOINTS),
    )
    parser.add_argument("--out", default="docs/p07_result.json")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
