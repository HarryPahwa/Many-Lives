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

Latest verification after the follow-up hardening work:

- 555 tests passed.
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

The candidate count and output ceiling are read from `config/runtime_rules.yaml`. The current defaults are 10 candidates and 6,000 output tokens. Twenty candidates were tested, but generation commonly produced 4,000–5,000 output tokens and dominated turn latency. JEV scoring itself was generally sub-second in the observed runs; candidate generation was usually 5–13 seconds and became much slower when a structured-output retry occurred.

Candidate generation and scoring are replaceable seams with deterministic fakes used by tests. Both receive the same canonical model-world projection: the environment, full player state, player inventory and equipment, visible-character state and inventory, room items, runtime context, and relevant policy context. JEV never scores against a thinner or differently shaped view than the generator used.

If every generated candidate fails deterministic validation, the generator receives the distinct rejection reasons and regenerates once. `candidate_generation.all_rejected_retries` caps this separately from provider and structured-output retries. The retry is intended to correct malformed proposals, not bypass validation. If the bounded retry also yields no valid candidate, the turn is rejected with no mutation.

### 4.3 Why the fast path was restored

The initial merge routed every normal command through JEV. In offline tests, `FakeCandidateGenerator` produced valid but empty bundles, so a command such as `north` could be accepted and narrated without moving the player. The generated context also lacked enough authoritative topology information for a model to derive a destination safely.

The correction preserves deterministic interpretation for obvious commands while retaining JEV for ambiguity. This is both safer and cheaper, and it prevents model availability from controlling basic game mechanics.

Fast-path parsing is not allowed to turn an ungrounded interpretation into a premature rejection. If a phrase such as `use weapon on goblin` parses structurally but its generic reference cannot be grounded, it falls through to candidate generation. Grounded rule failures remain deterministic rejections. Name matching uses exact matches first and then a unique partial match, so `weapon` may resolve to a single `weapon 1`, and `goblin` to a single `tunnel goblin`, without hard-coding individual names. Ambiguous partial matches remain errors.

## 5. Mutation model and authorization boundary

### 5.1 Model-facing primitives

Model-generated candidates use a closed set of primitives:

- `MutateAttribute` — permitted numeric, status, disposition, light, or condition changes;
- `TransferEntity` — validated entity transfer between canonical locations;
- `MoveEntity` — movement only to a destination present in the authorized snapshot;
- `AppendEvent` — a proposed typed event payload and summary.

The validator rejects missing or hallucinated entities, inaccessible cells, protected fields, unapproved paths, invalid condition operations, application-only mutation kinds, and model-supplied execution metadata.

The generator uses a separate model-visible schema that omits application-owned execution fields entirely. This avoids requiring a strict-output provider to fabricate nullable commit metadata. Arbitrary `dict[str, Any]` payloads are not used for model events: the strict-schema conversion closed such dictionaries as empty objects, which silently forced every generated event payload to `{}`. Model events now use typed dialogue, combat, and general payloads that survive strict schema generation.

Dialogue payloads contain `npc_id`, optional verbatim `utterance`, `dialogue_intent`, and `npc_reaction`. Deterministic validation requires `npc_id` to identify a visible character and requires either an utterance or an intent. Human-readable summaries are never used as authoritative entity references.

Combat payloads contain one `hp_delta` source of truth. Application code converts a damaging delta into both a positive display `damage` value on the event and the canonical negative HP mutation. Duplicate model-proposed HP mutations for the same combat payload are discarded. This prevents event prose, displayed damage, and the database mutation from disagreeing.

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

Numeric model values are normalized at the deterministic boundary. Finite numeric strings such as `"-3"` are accepted as numbers; booleans, nulls, non-finite values, and strings such as `"ADD"` or `"lots"` are rejected. HP changes are applied arithmetically and clamped to `[0, max_hp]`. Multiple changes to the same document compile into one version-checked write, preventing the first field update from invalidating the second update's compare-and-set version.

When non-player HP reaches zero, application code derives `status = DEAD` and an `ENTITY_DIED` event. The model proposes damage but does not decide whether canonical death occurred. This was added after a live run reached `hp = 0` while leaving the goblin `ALIVE`, permitting an additional attack. Historical corrupted records are not silently rewritten by this rule.

### 5.4 Current spellcasting limitation

Magic is scaffolded but not connected to this proposal pipeline. The repository has MP, `CAST`, `SPELL_CAST`, spellbook fields, and runtime values for a three-MP/six-damage spell, but there is no complete deterministic cast resolver or model-facing typed spell payload. A live fireball attempt therefore produced null MP mutations; both the initial batch and its bounded regeneration were correctly rejected.

Until spellcasting is implemented, the generator must not be treated as authority to invent spell access, MP cost, or damage. A future implementation should follow the combat pattern: a typed spell proposal, followed by deterministic checks for known/carried spell, sufficient MP, configured cost, dodge, damage, and canonical events.

## 6. Persistent character conditions and entity semantics

### 6.1 Closed condition vocabularies

Physical conditions are limited to `BLEEDING`, `POISONED`, `BLINDED`, `STUNNED`, `CRIPPLED`, `BURNING`, and `EXHAUSTED`.

Mental conditions are limited to `CHARMED`, `FRIGHTENED`, `CONFUSED`, `ENRAGED`, and `TERRIFIED`.

Condition mutations accept only single-value `ADD` or `REMOVE` operations. Model-proposed whole-list replacement is rejected. Application is idempotent and conditions retain deterministic ordering.

Existing documents without condition fields remain compatible and project empty lists.

### 6.2 NPC, enemy, and boss distinctions

Harness projections preserve canonical `NPC`, `ENEMY`, and `BOSS` entity types. Hostile characters may receive dialogue or verbal-action attempts for narrative reactions, but they do not become friendly NPCs, receive NPC trust progression, offer friendly quests, or leak NPC-only facts.

The API and UI expose entity types and active condition tags. Browser rendering continues to use safe text-node updates.

### 6.3 Dialogue behavior and progression

The generator prompt is action-neutral rather than combat-first. It asks for outcome diversity appropriate to the interaction class: dialogue and social reactions, combat results, exploration discoveries, and inventory/use consequences. For `INITIATE_CONVERSATION`, a proposed NPC reaction must be spoken—such as a greeting, guarded question, or verbal refusal—rather than only a nod or stare. The narrator must include quoted speech for a committed `DIALOGUE` event; gestures may accompany but not replace it.

Dialogue currently progresses as independent player turns. Explicit follow-ups naming the NPC and topic are grounded well, and relevant event memories may return through context. There is not yet a canonical active-conversation frame containing partner, topic, unanswered question, or last response. Pronoun-only follow-ups such as `why?` or `ask them what happened next` may therefore depend on memory retrieval rather than deterministic conversational reference. A future conversation frame can improve continuity without granting the model state authority.

## 7. Configuration lifecycle

The former monolithic `config/balance.yaml` was intentionally split:

- `config/world_gen.yaml` contains run-once campaign and room-generation settings, including grid dimensions, placement constraints, starting player values, generation tables, and dresser limits.
- `config/runtime_rules.yaml` contains per-turn mechanics and calibration data, including combat, checks, inventory, disposition, conditions, context budget, and candidate-generation settings.

Campaign creation reads world-generation configuration. Room planning composes the portions it needs from both files. Runtime/JEV components read runtime rules. Environment overrides are `WORLD_GEN_FILE` and `RUNTIME_RULES_FILE`.

## 8. Observability

### 8.1 Turn traces

`logs/turn_traces.jsonl` records player input, every generated candidate, generation-attempt number, filter results, semantic scores, normalized weights, the selected bundle, and final acceptance. Rejected first batches remain visible when bounded regeneration succeeds.

### 8.2 Database diagnostics

`logs/db.log` records SQLite transaction and touched-table diagnostics. Turn updates avoid duplicate inserts during ordinary lifecycle transitions, reducing misleading duplicate-key noise.

Structured model telemetry records total cumulative latency across all attempts, rather than only the successful final attempt. Each failed attempt records bounded elapsed time, exception type, and error text without logging prompt or response contents. Provider/schema retries and all-candidates-rejected regeneration are distinct mechanisms with separate caps.

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
555 passed, 0 failed
```

Coverage includes SQLite lifecycle and durability, campaign-scoped operations, movement, combat, inventory, discovery, invariants, duplicate turns, concurrent serialization, mutation authorization and parity, JEV generation/scoring/selection, narration failure after commit, and browser behavior.

One Starlette/AnyIO alias deprecation warning remains and does not affect behavior.

## 11. Follow-up opportunities

- Decompose the large JEV branch in `TurnOrchestrator._take_turn_locked` into focused pipeline functions.
- Clarify type names for model proposal bundles versus authorized execution bundles.
- Add measured production latency and database-size benchmarks; the original planning estimates were not retained as verified facts.
- Add an explicit canonical conversation frame if pronoun-heavy multi-turn dialogue becomes a product requirement.
- Implement the simple spellbook path only when P1 magic is scheduled; do not approximate it with unconstrained model mutations.
- Consider a targeted PydanticAI migration for the model-output boundary. Typed outputs, output validators, and bounded validation feedback could replace custom schema/retry plumbing, but deterministic grounding, mutation authorization, seeded selection, and canonical application must remain project-owned. This is not a reason to migrate the rest of the harness wholesale.
