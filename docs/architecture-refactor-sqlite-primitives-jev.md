# Many-Lives Architecture Refactor: SQLite Migration, Mutation Primitives, and Jev Pipeline

**Status**: Planning & Pre-Implementation  
**Date**: September 2026  
**Authors**: Architecture Post-Mortem & Refactor Summary

---

## 1. Executive Summary & Motivation

Many-Lives was originally built during a timeboxed hackathon sprint using MongoDB Atlas and a single-model LLM adjudication pipeline (`gemini-2.5-flash-lite`). While the core invariant (*models propose, deterministic code decides*) held, two major architectural friction points emerged:

1. **Persistence & Operational Overhead**: Cloud-hosted MongoDB Atlas introduced multi-document replica set transaction complexity, external network roundtrips (30–100ms per turn), connection timeouts, and debugging friction for a compact, local turn-based game.
2. **Action Rigidity vs. Hallucination**: A strict verb whitelist (`MOVE`, `ATTACK`, `TAKE`, `DROP`, `EQUIP`, `INTERACT`, `TALK`) caused frequent `"Unsupported action"` rejections on expressive player inputs. Conversely, unconstrained LLM writes would violate game state integrity.

This refactor replaces MongoDB with **local SQLite in WAL mode** and transitions the domain engine from hardcoded verb handlers to **generic state mutation primitives** scored by **TypeSafe Jev (System 1 judgment)** and sampled via **deterministic weighted RNG**.

---

## 2. Core Architectural Changes

### A. Persistence: MongoDB Atlas $\to$ SQLite (WAL Mode)
- **Local Embedded Database**: Replaces PyMongo/Atlas with standard Python SQLite (`sqlite3` / `aiosqlite`) storing campaign state in `dungeon.db`.
- **Hybrid Schema with JSON**: Tables for `campaigns`, `cells`, `entities`, `events`, `turns`, and `memories` use structured primary keys with JSON columns for nested feature trees and event payloads.
- **Zero-Network Atomic Transactions**: Multi-table state mutations execute in $<1\text{ ms}$ inside atomic `with transaction(conn):` blocks.
- **Zero-I/O In-Memory Testing**: Test suite runs against `:memory:` SQLite, enabling 100% offline, deterministic CI runs.
- **Local DB Logging**: Structured SQL execution and transaction logging to `logs/db.log`.

### B. Domain Engine: Verb Whitelists $\to$ Atomic Mutation Primitives
Instead of anticipating every verb a player might type, the domain engine implements a closed set of 4 algebraic state mutation primitives:
1. `MutateAttribute(target_id: str, path: str, value: Any)` (e.g. modify HP, change feature state like `light_state = "lit"`, update condition)
2. `TransferEntity(entity_id: str, from_ref: str, to_ref: str, kind: LocationKind)` (e.g. move item from chest to inventory)
3. `MoveEntity(entity_id: str, target_cell_id: str)` (e.g. player movement)
4. `AppendEvent(event_type: str, payload: dict, summary: str)` (e.g. log combat rolls, dialogue, state changes)

The engine validates physical and spatial invariants (target presence in room, non-negative HP, valid feature properties) and applies the mutations.

### C. Adjudication: Multi-Candidate Generator + Jev Semantic Scoring
To resolve free-form player creativity without brittle regex or hallucinated stats:

```
[Player Free-Form Input: "kick dirt in the goblin's eyes"]
                          │
                          ▼
        [Stage 1: Multi-Candidate Generator (LLM)]
   Generates N=3-5 diverse candidate interpretations:
   - Candidate A (Distract): MutateAttribute(goblin, "status", "distracted") + Narration A
   - Candidate B (Attack):   MutateAttribute(goblin, "hp", hp - 2) + Narration B
   - Candidate C (Invalid):  MutateAttribute(torch, "light_state", "unlit") + Narration C
                          │
                          ▼
        [Stage 2: Deterministic Schema Filter (0ms)]
   Discards physically impossible candidates (e.g. Candidate C: no torch in cell)
                          │
                          ▼
        [Stage 3: Jev Semantic Scoring (~50ms)]
   Calls Jev System One (`jev_noul` / calibrated scoring):
   - Evaluates surviving candidates against Room Environment, Player Stats, and `runtime_rules.yaml`
   - Assigns calibrated probability weights: P(A) = 0.75, P(B) = 0.25
                          │
                          ▼
        [Stage 4: Seeded Weighted RNG Selection]
   Deterministically samples winning candidate (Candidate A)
                          │
                          ▼
        [Stage 5: Atomic SQLite Commit & Narration Output]
   Applies winning mutation bundle, logs trace to `logs/turn_traces.jsonl`, and returns Narration A
```

### D. Configuration Lifecycle Split
Splits the monolithic `config/balance.yaml` into two distinct files:
- **`config/world_gen.yaml`**: Run-once campaign setup (grid dimensions, key counts, boss distance, starting player stats).
- **`config/runtime_rules.yaml`**: Per-turn execution tuning (base DCs, damage variance, disposition deltas, potion values) passed as lean calibration context to Jev.

---

## 3. Directory Impact Summary

```
app/
├── domain/       ──> Refactored: Replaces rules.py verb whitelist with primitives.py executor & schema filter.
├── persistence/  ──> Refactored: Replaces mongo.py with sqlite.py; updates repositories.py to SQLite + JSON.
├── world/        ──> Updated: room_planner.py reads world_gen.yaml instead of monolithic balance config.
├── harness/      ──> Refactored: adjudicator.py becomes multi-candidate generator; adds Jev semantic client.
├── services/     ──> Refactored: turn_orchestrator.py runs Generate -> Filter -> Jev -> RNG -> Commit pipeline.
├── api/          ──> Unchanged: HTTP routes and error envelopes remain identical.
└── ui/           ──> Unchanged: Static frontend receives narrated turns and debug traces.
```

---

## 4. Observability & Debugging Enhancements

1. **`logs/turn_traces.jsonl`**: Single append-only JSONL log containing the full turn decision trace:
   - Raw player input.
   - All generated candidate mutation bundles.
   - Schema validator pass/fail diagnostics.
   - Jev calibrated probability scores.
   - RNG roll result and winning candidate selection.
2. **`logs/db.log`**: Query execution, transaction timing, and connection event logs.
3. **CLI Inspection Tools**: Enhanced `scripts/game_state.py` and `scripts/inspect_turns.py` reading directly from local SQLite.

---

## 5. Implementation Roadmap & Active Plans

- `.pi/plans.local/sqlite-persistence-migration.md` — Autonomous task: SQLite repository rewrite, WAL configuration, in-memory testing.
- `.pi/plans.local/mutation-primitives-jev-pipeline.md` — Core domain refactor: mutation primitives, multi-candidate generator, Jev scoring, and weighted RNG execution.

*(This document will be updated with post-implementation benchmarks and latency measurements once the migration is complete.)*
