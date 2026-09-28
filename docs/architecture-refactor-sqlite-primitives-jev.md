# Many-Lives Architecture Refactor: SQLite, Mutation Bundles, and JEV

**Status:** Implemented and verified

**Date:** September 2026

**Purpose:** Architecture record and implementation post-mortem

## 1. Executive summary

Many-Lives now uses an embedded SQLite persistence layer and a two-route action pipeline:

- Structurally obvious commands use deterministic parsing and the established rules engine.
- Free-form commands use multi-candidate mutation generation, deterministic filtering, JEV semantic scoring, and seeded selection.

Both routes converge on a shared mutation-application boundary before canonical state is committed. Models may propose candidate outcomes, but they cannot write the database, supply authoritative commit metadata, bypass version checks, or use application-only mutation operations.

The refactor also split world-generation configuration from runtime rules and added persistent physical and mental character conditions while preserving the semantic distinction between NPCs, enemies, and bosses.

Final verification after integration:

- 540 tests passed.
- The run included 14 Playwright browser tests, all integration tests, probes, and unit tests.
- Tests ran with fake model components and required no external database or model credentials.

## 2. Architectural invariants

The central invariant remains unchanged:

> Models interpret and propose; deterministic application code alone establishes canonical state.

The implementation preserves this through the following rules:

1. Models receive scoped world context but no database-write capability.
2. Model-originated mutation bundles use a closed, validated vocabulary.
3. Entity references, accessible cells, attribute paths, condition values, and protected fields are checked deterministically.
4. Campaign scope, document versions, turn identity, event IDs, event ordering, and commit metadata are application-owned.
5. Consequential changes and their events commit atomically.
6. Duplicate turn IDs replay stored results rather than applying mutations twice.
7. Every persistence query, including history and memory access, remains scoped by `campaign_id`.

## 3. SQLite persistence

### 3.1 Why SQLite

MongoDB Atlas added network dependency, connection setup, transaction complexity, and debugging overhead to a compact local turn-based world. SQLite provides local durability, atomic multi-table transactions, and fast isolated tests without changing the domain’s repository contracts.

### 3.2 Storage model

`app/persistence/sqlite.py` implements a narrow document-store adapter over ordinary SQLite tables. Canonical documents remain JSON-shaped while commonly queried fields are also stored in indexed columns.

The persisted areas include campaigns, cells, entities, events, turns, memories, quests, policies, evaluations, and visual metadata used by later features.

Indexes support campaign, cell, entity, location, event-order, turn, and memory lookups. `app/persistence/repositories.py` retains campaign-scoped repository operations and reconstructs immutable world views without leaking SQLite behavior into `domain/`.

### 3.3 Transactions and concurrency

The SQLite layer provides:

- atomic transaction and nested-savepoint handling;
- optimistic document-version checks;
- atomic turn mutation, insertion, event, and campaign-counter updates;
- durable turn reservation and replay for idempotency;
- `:memory:` databases for tests;
- local diagnostics in `logs/db.log`.

File-backed databases use SQLite WAL configuration. Tests use isolated in-memory databases and require no persistence service.

### 3.4 Configuration

Persistence is configured with `SQLITE_DB_PATH`; the application defaults to a local `dungeon.db` file.

## 4. Action routing

### 4.1 Deterministic fast path

Recognized commands such as movement, observation, explicit inventory operations, and direct combat commands do not need semantic candidate selection.

```text
player input
    -> deterministic parser
    -> ActionIntent
    -> deterministic rules engine
    -> accepted Resolution
    -> deterministic MutationBundle adapter
    -> mutation application
    -> atomic SQLite commit
```

The rules engine remains responsible for all authoritative calculations and compound consequences, including:

- topology and boss-door checks;
- destination selection and discovery;
- combat rolls, damage, death, and environmental responses;
- XP, level progression, counters, and disposition;
- inventory capacity, stacking, equipment, consumption, and item insertion;
- feature changes, key submission, treasure, and campaign completion.

The adapter does not recalculate those outcomes. It losslessly converts the resulting canonical document mutations, inserts, and events into an application-owned bundle so deterministic and JEV turns share the application boundary.

### 4.2 Free-form JEV pipeline

Unrecognized or expressive input uses the candidate pipeline:

```text
free-form player input
    -> build bounded campaign-scoped context
    -> generate N candidate MutationBundles
    -> deterministic schema and invariant filter
    -> JEV semantic probability scoring
    -> normalized weights
    -> seeded TurnRng selection
    -> mutation application
    -> atomic SQLite commit
    -> narration from committed state
```

The default candidate count is read from `config/runtime_rules.yaml`. Candidate generation and scoring are replaceable seams with deterministic fakes used by tests.

### 4.3 Why the fast path was restored

The initial merge routed every normal command through JEV. In offline tests, `FakeCandidateGenerator` produced valid but empty bundles, so a command such as `north` could be accepted and narrated without moving the player. The generated context also lacked enough authoritative topology information for a model to derive a destination safely.

The correction preserves deterministic interpretation for obvious commands while retaining JEV for ambiguity. This is both safer and cheaper, and it prevents model availability from controlling basic game mechanics.

## 5. Mutation model and authorization boundary

### 5.1 Model-facing primitives

Model-generated candidates use a closed set of primitives:

- `MutateAttribute` — permitted numeric, status, disposition, light, or condition changes;
- `TransferEntity` — validated entity transfer between canonical locations;
- `MoveEntity` — movement only to a destination present in the authorized snapshot;
- `AppendEvent` — a proposed typed event payload and summary.

The validator rejects missing or hallucinated entities, inaccessible cells, protected fields, unapproved paths, invalid condition operations, application-only mutation kinds, and model-supplied execution metadata.

Model-facing paths such as `stats.hp` are mapped to the canonical repository shape, such as `character.hp`. Transfers and movement produce canonical `location` objects with `kind`, `ref_id`, and `slot`.

### 5.2 Application-owned mutations

Deterministic resolutions require a richer, lossless representation. Application-only bundle operations carry:

- canonical document patches with set, increment, and add-to-set fields;
- canonical document inserts;
- fully formed typed events;
- expected turn and campaign versions;
- touched entity and cell IDs;
- rejected effects and current-cell metadata.

These operations cannot be submitted by model-originated bundles. The distinction lets deterministic rules express complex state transitions without granting equivalent authority to a model.

### 5.3 Canonical schema compatibility

Early primitive tests used simplified mock fields such as `stats.hp`, `location_kind`, and `location_id`. Repository-backed parity work corrected the executor to understand real entity IDs, cell IDs, nested character documents, and canonical location objects. The persistence schema was not changed to accommodate the mutation engine; the mutation engine was adapted to the established schema.

## 6. Persistent character conditions and entity semantics

### 6.1 Closed condition vocabularies

Physical conditions are limited to `BLEEDING`, `POISONED`, `BLINDED`, `STUNNED`, `CRIPPLED`, `BURNING`, and `EXHAUSTED`.

Mental conditions are limited to `CHARMED`, `FRIGHTENED`, `CONFUSED`, `ENRAGED`, and `TERRIFIED`.

Condition mutations accept only single-value `ADD` or `REMOVE` operations. Model-proposed whole-list replacement is rejected. Application is idempotent and conditions retain deterministic ordering.

Existing documents without condition fields remain compatible and project empty lists.

### 6.2 NPC, enemy, and boss distinctions

Harness projections preserve canonical `NPC`, `ENEMY`, and `BOSS` entity types. Hostile characters may receive dialogue or verbal-action attempts for narrative reactions, but they do not become friendly NPCs, receive NPC trust progression, offer friendly quests, or leak NPC-only facts.

The API and UI expose entity types and active condition tags. Browser rendering continues to use safe text-node updates.

## 7. Configuration lifecycle

The former monolithic `config/balance.yaml` was intentionally split:

- `config/world_gen.yaml` contains run-once campaign and room-generation settings, including grid dimensions, placement constraints, starting player values, generation tables, and dresser limits.
- `config/runtime_rules.yaml` contains per-turn mechanics and calibration data, including combat, checks, inventory, disposition, conditions, context budget, and candidate-generation settings.

Campaign creation reads world-generation configuration. Room planning composes the portions it needs from both files. Runtime/JEV components read runtime rules. Environment overrides are `WORLD_GEN_FILE` and `RUNTIME_RULES_FILE`.

## 8. Observability

### 8.1 Turn traces

`logs/turn_traces.jsonl` records player input, generated candidates, filter results, semantic scores, normalized weights, the selected bundle, and final acceptance.

### 8.2 Database diagnostics

`logs/db.log` records SQLite transaction and touched-table diagnostics. Expected duplicate turn claims currently use exception-based control flow and may create noisy error entries even when replay behavior is correct.

### 8.3 Inspector

The browser inspector distinguishes `FAST` from `JEV_PIPELINE`, shows bounded-context manifests, accepted/rejected effects, persisted event IDs, narration verification, and recorded model calls. JEV uses bundles rather than the retired single adjudicator proposal.

## 9. Code map

```text
app/
├── config.py                         # SQLite and service settings
├── domain/
│   ├── rules.py                      # deterministic authoritative mechanics
│   ├── mutations.py                  # model and application mutation types
│   ├── mutation_validator.py         # authorization and application
│   ├── mutation_adapter.py           # deterministic Resolution -> bundle
│   └── picker.py                     # seeded weighted candidate selection
├── persistence/
│   ├── sqlite.py                     # connection, schema, transactions, adapter
│   ├── repositories.py               # campaign-scoped persistence operations
│   └── views.py                      # immutable canonical world views
├── harness/
│   ├── candidate_generator.py        # multi-candidate generation seam
│   └── jev_scorer.py                 # semantic scoring seam
├── services/
│   ├── sqlite_engine.py              # domain/persistence integration
│   ├── turn_orchestrator.py          # FAST versus JEV routing and lifecycle
│   └── turn_trace_logger.py          # JSONL decision traces
└── ui/static/                        # inspector and condition rendering

config/
├── world_gen.yaml
└── runtime_rules.yaml
```

## 10. Verification

The integrated architecture was verified with:

```bash
uv sync --frozen --all-extras
.venv/bin/python -m pytest
```

Result:

```text
540 passed, 0 failed
```

Coverage includes SQLite lifecycle and durability, campaign-scoped operations, movement, combat, inventory, discovery, invariants, duplicate turns, concurrent serialization, mutation authorization and parity, JEV generation/scoring/selection, narration failure after commit, and browser behavior.

One Starlette/AnyIO alias deprecation warning remains and does not affect behavior.

## 11. Follow-up opportunities

- Decompose the large JEV branch in `TurnOrchestrator._take_turn_locked` into focused pipeline functions.
- Clarify type names for model proposal bundles versus authorized execution bundles.
- Record candidate-generator and JEV-scorer calls in inspector model-call telemetry.
- Reduce error-level logging for expected duplicate turn claims.
- Add measured production latency and database-size benchmarks; the original planning estimates were not retained as verified facts.
