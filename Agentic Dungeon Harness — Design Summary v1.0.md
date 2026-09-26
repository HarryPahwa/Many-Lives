# Agentic Dungeon Harness — Design Summary v1.0

Sep 26, 2026 · @Mike Sandare

## 1. Purpose, scope, and constraints

The team is building a persistent-world agent harness for the MongoDB Harness Engineering & Model Wrangling hackathon; a turn-based, text-first dungeon raid is the demonstration environment. The primary target is Statement Two (Long Horizon Engineering). Statement One (Recursive Harnessing) is claimed only if an automatic context-policy promotion or rollback runs and is shown with before/after metrics.

The central invariant is: **models may interpret, propose, describe, and reason; only deterministic application code may establish or mutate canonical game state.**

This summary condenses the Architecture v1.0 technical design agreed by the team. The full implementation specification for coding agents is the companion Markdown file.

| Constraint (participant guide) | Design consequence |
| --- | --- |
| Hacking runs 10:30–17:00 on 26 Sep; submissions due 17:00; first-round judging 17:15–18:45 | Integrated P0 loop early; feature freeze at 16:00 |
| Project must be new work built entirely during the event | No pre-written code, prompts, schemas, or demo data; this document is planning only |
| Finalists must build in the supplied Atlas Sandbox; Atlas must be a core component | State, events, memory, policies, and evaluations all live in Atlas |
| Sandbox tier is not stated | Design to public Free-tier limits until inspected: 0.5 GB storage, 500 connections, 100 ops/s, 3 Search/Vector indexes |
| About 3 minutes of live demo plus 1–2 minutes of Q\&A; no slide presentation; 1-minute video; public repository | Demo shows process kill, campaign resume, correct state, and the context inspector |
| Streamlit apps and dashboard-centred projects are listed anti-projects | Plain web UI; the context inspector is a secondary panel |

The project does not claim an empirical billion-token campaign. The demonstrable property is that stored history grows while each model call's working context stays bounded, and correctness is measured.

## 2. System architecture

The harness is ordinary application code around three model calls (room dresser, adjudicator, narrator); the rules engine is the only component that decides outcomes, and the repository is the only component that writes them.

&#91;embedded content: Harness architecture · request path, authority boundary, Atlas stores\]

Known commands skip the model entirely. Free-form input goes through the context builder and adjudicator, which return a typed proposal that the engine re-validates. State and events commit in one transaction; memory extraction and narration run after commit and cannot corrupt state. No agent framework is required; LangGraph is an optional substitution for the orchestrator if it saves time, and OpenClaw is not used.

| Component | Kind | Responsibility | Writes canonical state |
| --- | --- | --- | --- |
| Turn orchestrator | Code | Runs the fixed turn lifecycle | Only through engine and repository |
| Fast-path parser | Code | Maps obvious commands to typed intents | No |
| Context builder | Code | Selects exact state, recent events, and memory per policy | No |
| Adjudicator | Model | Interprets free-form intent; proposes allowlisted effects and a bounded modifier | No |
| Room planner | Code | Chooses room archetype and stats at first entry | No |
| Room dresser | Model | Proposes names, environment, features, identities | No |
| Rules engine + RNG | Code | Validates proposals, rolls dice, resolves mechanics | Yes |
| Repository | Code | Atomic, campaign-scoped persistence | Yes |
| Event log | Atlas | Immutable consequential history | Append only |
| Memory pipeline | Code (+ embeddings) | Derives semantic memories from events | No world-state writes |
| Narrator | Model | Prose plus machine-checkable claims from committed results | Never |
| Invariant checker, narration verifier | Code | Detect invalid state and contradicted claims | No |
| Harness evaluator / optimizer | Code | Runs probes; promotes or rolls back context policies | Policy state only |

## 3. Turn lifecycle, room generation, and resume

Each turn is one idempotent request that resolves mechanics, commits state and events atomically, and only then narrates.

**Turn lifecycle**

1. The client sends `turn_id`, `campaign_id`, `player_id`, and free text. A `turn_id` that already committed returns the stored result; effects are never applied twice.
2. Known commands (for example `north`, `attack goblin`, `take brass key`, `equip iron sword`, `use mana potion`) are parsed in code. Other input goes to the context builder, which assembles context for that action class under the active policy; the adjudicator returns an `ActionProposal`.
3. The rules engine recomputes every precondition, rejects effects outside the allowlist, rolls code RNG, resolves the action, then runs the environment-response phase (hostile attacks, timed states).
4. The repository commits entity, cell, and campaign updates plus the turn's events in one transaction with optimistic version checks.
5. After commit, consequential events are queued for memory extraction. A failed extraction is retried and never rolls back state; recent exact events cover the gap on the next turn.
6. The narrator receives committed events and current state and returns prose plus machine-checkable claims. The deterministic verifier compares claims to state. If narration fails, it is regenerated from stored events.

**Room generation at first entry**

1. Campaign creation fixes only the seed, a 7×7 grid, edge topology (randomized spanning tree plus restored edges), a boundary spawn, a boss cell at least 5 graph steps away, 6 key reservations (X = 3), and the distance-to-boss map.
2. On first entry, the room planner (code) chooses the archetype — empty, NPC, enemy, item, or an allowed combination — from the danger tier, reservations, and generation policy.
3. The room dresser (model) proposes the static environment, features with property tags, and names and identities. Code assigns all stats from tier tables.
4. Code validates schema and mechanics, retries a bounded number of times, and falls back to a deterministic room that still honours reservations.
5. The accepted room is persisted and marked generated. It is never regenerated; later changes update mutable feature and entity state only.

**Resume.** Loading a `campaign_id` reads the campaign, player, current cell and entities, active quests, recent events, relevant memories, and the active context policy, then builds a fresh model context. A campaign is not a chat transcript, and none is stored or needed.

## 4. Persistence model

Atlas holds four separate concerns: current state (what is true now), events (what happened), semantic memory (which history may matter now), and harness experience (how well the harness is working). Present-tense questions are answered from current state, never from vector similarity.

| Collection | Layer | Holds | Key indexes |
| --- | --- | --- | --- |
| `campaigns` | State | Seed, grid, edge topology stored once as an adjacency list, spawn, boss, boss door, turn counters, turn order, active policy version | `{status, updated_at}` |
| `cells` | State | Reservations, danger tier, archetype, immutable static environment, mutable features with property tags and state | unique `{campaign_id, x, y}`; `{campaign_id, generated}` |
| `entities` | State | Players, NPCs, enemies, items in one discriminated collection; one location per movable entity; stats; per-player disposition; stack data | `{campaign_id, entity_type}`; `{campaign_id, location.ref_id, location.kind}`; `{campaign_id, character.status}` |
| `events` | Events | Immutable consequential history ordered by `(turn_sequence, event_index)`, with `memory_status` | unique `{campaign_id, turn_sequence, event_index}`; unique `{campaign_id, turn_id, event_index}`; entity and cell lookups |
| `memories` | Semantic memory | Curated memory text, source event IDs, entity/cell/type metadata, embedding | One Vector Search index: `embedding` plus filter fields `campaign_id`, `entity_ids`, `cell_id`, `memory_type` |
| `context_policies` | Harness experience | Versioned context rules per action class, parent version, status | `{status}` |
| `evaluations` | Harness experience | Probe results and aggregate metrics per policy version | `{policy_version, created_at}` |
| `turns` | Harness experience | Per-turn record: input, proposal, accepted effects, narration, claims, verifier result, token and latency telemetry, context manifest | unique `{campaign_id, turn_id}` |
| `quests` (P1) | State | Template, target or reservation, issuer, status such as `FAILED_ISSUER_DEAD` | `{campaign_id, status}` |

The `turns` collection is an implementation addition in this revision. It gives idempotent replay, per-call metrics, and the context inspector one home without changing the architecture.

- Every document carries `campaign_id`, and every query filters on it, including `$vectorSearch`. Vector pre-filter fields must be declared as filter fields in the index.
- An item's location is stored only on the item. Inventories are queries, not lists. Keys and unique items never stack; consumables stack to 3.
- Memories are written only for consequential events: betrayal, promise, quest agreement, NPC injury or death, key discovery, important transfer, major combat outcome, boss interaction. Movement and equipment toggles stay in the event log only.
- Saved games are separate campaign namespaces. Branching saves and world cloning are out of scope.
- Automated Embedding runs on free clusters but is limited to 3 requests/min and 2,000 tokens/min without a payment method, so embeddings come from the model provider and are written after commit.

## 5. Decision register

All decisions below are frozen for the event; numeric values are configuration defaults that may be tuned without an architecture change.

| Area | Decision |
| --- | --- |
| Authority | Models propose typed data; the rules engine decides; the repository writes. No model gets a database-write tool. |
| Orchestration | Plain application code. LangGraph is an optional substitute if it saves time; OpenClaw is not used. |
| Model roles | Room dresser, adjudicator, narrator. A second-model semantic verifier is optional and secondary. |
| Stack | Python 3.12, FastAPI, Pydantic v2, PyMongo, minimal browser UI (TypeScript/Zod is an equivalent option). |
| Model access | Provider-agnostic client; structured outputs with strict JSON Schema; on OpenRouter, `require_parameters: true`. |
| Randomness | Seeded application RNG only; never model output. |
| World | 7×7 grid, walls on edges, X = 3 keys required, 6 key reservations, boundary spawn at least 5 graph steps from the boss. |
| Difficulty | Tier 1–5 from shortest-path distance to the boss. |
| Rooms | Code picks the archetype at first entry; the model dresses it; accepted rooms are never regenerated. |
| Starting stats | HP 20, MP 6, Attack 5, Defense 2, Speed 4, Dodge 10%, Skill 3. |
| Level-up | Choose one: HP +5, MP +2, Attack +1, Defense +1, Speed +1, Dodge +2 points (cap about 40%), Skill +1. |
| Damage | Dodge check; otherwise max(1, attack + weapon + variance − defense − armor), variance in {−1, 0, +1}. Integers only. |
| Speed | Orders the first hostile encounter, stealing, and opportunity situations; not multiplayer turn order. |
| Flee | The highest-priority hostile gets one opportunity attack; the move succeeds if the player survives. |
| Uncertain actions | d20 + stat modifier + relationship/environment modifier + model approach modifier in \[−2, +2\], compared with a DC. |
| Death | Respawn at spawn with full HP and MP; drop one unit from a random occupied carried slot, else a random equipped item; the drop stays on the death-room floor, guarded by any remaining hostile. |
| Death XP | Always non-zero, including repeat deaths to the same encounter: 20% of the level threshold + 2% per new cell since last death (cap +10%) + 0–10% combat progress; maximum 40%. |
| Persistence of damage | Enemy and boss damage persists through player death. |
| MP | Full on death; mana potions restore MP; no passive regeneration. |
| Inventory | 6 carried slots plus weapon and armor slots; consumables stack to 3; keys and unique items never stack. |
| Boss door | Unlocks permanently once X distinct keys are submitted; submitted keys are consumed. Victory is claiming the treasure after the boss is defeated. |
| Creative actions | Tags: flammable, breakable, movable, heavy, container, concealing, light\_source. States: open/closed, locked/unlocked, intact/broken, upright/overturned, lit/unlit. Burning (3 turns, 1 damage per turn, no spread) is P2. |
| NPC knowledge | NPCs see only their own state, room, disposition, recent events, relationship memories, and authorized facts. Revealed facts come from a fact ID and are stored, for example a cell marked `RUMORED`. |
| Quests (P1) | Templates: retrieve item, kill entity, obtain loot, collect N. Feasibility is proven before activation; never a hidden item on the issuer; issuer death sets `FAILED_ISSUER_DEAD`. |
| Learning | Context-policy evaluation is the learning mechanism. Difficulty adaptation (a Director) is rejected as the learning story because it confounds the measurement. |
| Multiplayer (P2) | Shuffled global turn order; PvP uses the same engine; the unlocked door is shared; first to claim the treasure wins. Remote play is post-hackathon. |

## 6. Harness learning and evaluation

The harness's long-term objective is to keep the campaign coherent, mechanically valid, and solvable while context cost stays bounded as history grows. It learns by evaluating versioned context policies against a fixed probe suite, not by changing game difficulty.

**Hard metrics** recorded per turn and per probe: narration contradiction rate, unsupported or invented entity claims, invalid `ActionProposal` rate, rejected-effect rate, invariant failures, resume-fidelity failures, context tokens per model call, retrieval hit rate, and model and vector-search latency.

**Probe suite.** 12–20 fixed scenarios with known ground truth, for example: an item moved from a chest to the player; an NPC killed while an old memory says alive; a chair overturned and later revisited; an injured, hostile NPC; a key transferred from an NPC; a quest promise from many turns earlier; 10,000 irrelevant prior events; a process stop and resume. Each policy version records contradiction rate, retrieval success, token cost, and latency. The suite is an engineering regression harness, not evidence of general model reliability.

**Bounded policy change loop (P0.5)**

1. Measure policy vN on the probe suite.
2. Detect the dominant failure category, for example item-related contradictions in social turns.
3. Propose one allowed mutation: make a context component mandatory, conditional, or disabled; change the recent-event window; change vector `top_k`; change allowed memory types; change entity or cell filter requirements.
4. Validate the candidate policy against its schema.
5. Re-run the same probes.
6. Promote vN+1 if contradictions fall and the token and latency increase stays within budget; otherwise roll back.

Arbitrary prompt or code rewrites are not allowed mutations. A Statement One claim requires the promotion to happen automatically, with the before/after metrics shown. A synthetic history script (about 10,000 events and memories) shows stored history growing while working context stays within its configured budget.

## 7. Priorities and cut criteria

P0 must work for submission; nothing from a lower tier starts until the P0 loop runs end to end. Persistence, context construction, and evaluation are never traded for extra game features.

| Tier | Scope |
| --- | --- |
| P0 — submission | Campaign create/list/resume; connected seeded grid; spawn, boss, and key reservations; lazy persistent rooms; movement and fog-of-war minimap; cells and entities; combat, death, and drop; bounded inventory; take, equip, use; append-only events; idempotent turns; adjudicator for free-form actions; narrator over committed state; NPC/history semantic memory with filtered Vector Search; fresh-session resume; core invariants and persistence integration tests |
| P0.5 — challenge completeness | Narration claims and deterministic verifier; probe suite; versioned context policies; hard metrics; one automatic promotion/rollback loop |
| P1 — after a stable loop | Social actions and Skill checks; XP and leveling; mana potion and one simple spellbook; quest allocator; context inspector polish; semantic verifier; synthetic long-history script |
| P2 — stretch | Bounded burning; AI stress player; hot-seat multiplayer and PvP; corpse decay; model-proposed policy candidates; optional LangGraph orchestration |
| Out of scope | Remote multiplayer, authentication, large spell system, full D&D rules, mobile clients, physics simulation, roaming NPCs, self-modifying code, generic agent framework |

| Time | Trigger | Action |
| --- | --- | --- |
| 12:45 | Persistent room revisit not working | Cut all P1 and P2 work |
| 14:15 | Combat plus restart/resume not working | Cut quests, leveling, spells, social mechanics |
| 15:00 | Vector Search not working | Developer B builds only one filtered NPC/history retrieval path |
| 15:30 | P0 unstable | Skip automatic policy optimization; keep probes and metrics; do not claim Statement One |
| 16:00 | Demo flow not deterministic | Freeze a known campaign and seed; remove stochastic branches from the live demo |

## 8. Workback plan for 26 September

The plan works back from the 17:00 submission: feature freeze at 16:00, a stable P0 demo scenario by 15:15, and first persistent room revisit by 13:00. Each row ends with an exit criterion that must hold before the next row starts.

| Time | Developer A — engine | Developer B — harness | Developer C — product | Exit criterion |
| --- | --- | --- | --- | --- |
| 09:00–10:30 | Sandbox access, generic installs; no project code | Provider keys; list structured-output-capable models | Discord, sponsor accounts, repo naming | Roles and contracts understood |
| 10:30–11:00 | DB connection, base domain types, campaign skeleton | Model client, JSON schemas, provider check | UI shell, API client, map placeholder | Repo runs; shared contracts compile |
| 11:00–12:00 | Topology and BFS; campaign, cell, entity repositories | Room dresser, context skeleton, adjudicator stub | Create/resume UI, movement and chat loop | Create a campaign and move through persistent empty cells |
| 12:00–13:00 | Lazy room commit, events, item location model | Structured room generation and narration | Render generated room, minimap, character panel | First generated room persists on revisit |
| 13:00–13:30 | Integration or lunch | Integration or lunch | Integration or lunch | No new architecture decisions |
| 13:30–14:30 | Combat, death, drop, idempotency | Context routing, semantic memory, vector index and query | Inventory UI, restart/resume path | Damage, kills, and items survive restart |
| 14:30–15:15 | Invariants, transaction hardening | Narration claims, memory retry | Context inspector, integration tests | Core P0 demo scenario works end to end |
| 15:15–16:00 | Bug fixes, probe support | Policy v1, probes, optional automatic candidate | Demo campaign setup, stress driver | P0 stable; P0.5 attempted only now |
| 16:00–16:30 | Freeze; regression | Freeze; regression | Demo rehearsal; record assets | No new features unless fixing a blocker |
| 16:30–16:50 | Support | Support | Record 1-minute video; confirm repo is public | Submission artifacts ready |
| 16:50–17:00 | Submit | Submit | Submit | Submitted before the deadline |

The pre-hacking row is limited to non-executable preparation. Scaffolding, prompts, schemas, fixtures, and indexes wait until 10:30 unless the organizers confirm otherwise.

## 9. Work split and integration contracts

Each developer owns one layer, but all three integrate against shared contracts from 11:00. An integrated P0 loop by early afternoon is worth more than three polished, disconnected components at 16:00.

| Developer | Owns | Main modules | Provides to others |
| --- | --- | --- | --- |
| A — Engine and persistence | Mongo connection and repositories, topology and campaign creation, cell/entity/event schemas, rules engine and RNG, combat, death, inventory, transactions, idempotency, resume, invariant checker | `domain/`, `world/topology.py`, `world/room_planner.py`, `persistence/`, `services/campaign_service.py` | `apply_intent()` and `commit_turn()`; repository read functions for the context builder |
| B — Harness and memory | Model client and structured outputs, room dresser, adjudicator, context builder and policies, memory extraction, embeddings and Vector Search, narrator and claims, verifier, probes and evaluator | `harness/` | `ActionProposal` and `RoomSpec` producers; `narrate()`; context manifest for the inspector |
| C — Product and integration | Web UI, chat loop, fog-of-war minimap, character and inventory panel, campaign create/resume, API wiring, context inspector, integration and stress drivers, demo, video, submission | `api/`, `services/turn_orchestrator.py`, `ui/`, `tests/integration/`, `scripts/` | The running end-to-end loop; demo campaign; submission |

**Contracts to agree between 10:30 and 11:00** (defined in code during the event, not before):

- ID conventions: `campaign_id`, `cell_id` = `cell_{x}_{y}`, stable entity IDs, client-generated `turn_id`.
- Domain types: `ActionIntent`, `ActionProposal`, `Effect` (allowlist), `Event`, `RoomSpec`, `NarrationResult` (prose plus claims), `ContextPolicy`, `TurnResult`.
- Repository interface: campaign-scoped reads and one `commit_turn` transaction.
- API: `POST /api/campaigns`, `GET /api/campaigns`, `POST /api/campaigns/{id}/resume`, `POST /api/campaigns/{id}/turns`, `GET /map`, `GET /player`, `GET /debug/context`.

Developer C owns the turn orchestrator because it is the integration seam; A and B each expose one call into it. Stubs are allowed for the first hour so that C can wire the loop before the real engine and model calls land.

## 10. Demo plan, risks, and open items

The 3-minute demo proves continuity comes from the harness, not a live transcript: the process is killed, the campaign resumes from Atlas, and the state is correct.

**Demo campaign** (built during the event after 15:15, fixed seed): Mara is in a known room, damaged and hostile; a chair is overturned; a key has moved from Mara or a container to the player; another enemy is dead.

1. Show the running game and the room's state.
2. Terminate the application process and model session.
3. Restart and resume by `campaign_id`.
4. Return to the room: injury, hostility, the moved key, and the overturned chair are correct.
5. Open the context inspector: exact state, recent event IDs, and selected memory IDs with scores — not the full history.
6. If P0.5 exists, show the stored before/after metrics for one policy promotion or rollback.

The 1-minute video shows the longer causal chain in accelerated form.

| Risk | Mitigation |
| --- | --- |
| Sandbox tier is smaller than expected | Selective memory writes, one vector index, synthetic history loaded early |
| Chosen model lacks strict structured outputs | `require_parameters: true`; Pydantic validation, bounded retry, deterministic fallback |
| Vector index is not yet queryable after creation | Create it by 14:00 and poll status; fall back to exact state plus recent events |
| Slow turns | Fast path for known commands; at most one adjudicator call; narration after commit |
| Random drop or model output breaks the demo | Fixed seed and prepared campaign |
| New-work rule is interpreted strictly | Ask organizers in #questions before creating any project artifact |

**Open items for kickoff:** the Atlas Sandbox tier and limits; available models and credits; the organizers' ruling on pre-event planning documents; whether LangGraph is used (decide by 11:00, default no).

**Sources:** participant guide (provided by the team); [Atlas Free cluster limits](https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/); [Automated Embedding models and limits](https://www.mongodb.com/docs/vector-search/crud-embeddings/automated-embedding/models/); [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs); [OpenAI Agents SDK — orchestration](https://openai.github.io/openai-agents-python/multi_agent/); [MongoDB — Agent Memory Inside the Harness](https://www.mongodb.com/company/blog/technical/agent-memory-inside-harness); [LLMs Are Bad Dice Players](https://arxiv.org/abs/2601.05414).
