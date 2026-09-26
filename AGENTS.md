# AGENTS.md — Many-Lives (Agentic Dungeon Harness)

Notes for anyone (human or agent) picking up this repo.

## What this is

A persistent-world **agent harness** demonstrated by a turn-based text dungeon.
The harness is the product; the game is the environment. Full spec:

- `docs/Agentic Dungeon Harness — Design Summary v1.0.md`
- `docs/Agentic_Dungeon_Harness_TDD_v1_1.md` — implementation spec (authoritative)

**Central invariant:** models may interpret, propose, describe, and reason;
only deterministic application code may establish or mutate canonical state.
No model gets database-write access. (TDD §5, §32.1.)

## Current status

**Scaffold only.** Repository structure (TDD §25), tooling, and the FastAPI app
shell are in place. No game logic yet — each module has a docstring naming its
responsibility, owner, and the TDD section it implements. `pytest` passes a
`/health` smoke test.

Recommended next slice (needs no external credentials): the pure `domain/` +
`world/` core with the §20.1 unit tests.

## Stack & environment

- Python 3.12. Local dev uses a conda env named **`many-lives`**
  (`conda activate many-lives`); deps installed via `pip install -e ".[dev]"`.
- FastAPI + Uvicorn, Pydantic v2, PyMongo, OpenAI SDK (pointed at OpenRouter),
  PyYAML. Tests: pytest.
- Run: `uvicorn app.main:app --reload` · Test: `pytest`
- Config via env vars / untracked `.env` (see `.env.example`). **Never commit
  secrets** — the repo is public and `.env` is git-ignored.

## Layer / dependency rule (TDD §6.2)

`domain` imports nothing from `harness`, `persistence`, or `api`.
`harness` may import `domain` types. `services` composes all layers.
Keep `domain/` free of I/O so the engine is unit-testable without a DB or model.

## Workstreams (TDD §27)

- **A — engine & persistence:** `domain/`, `world/`, `persistence/`,
  `services/campaign_service.py`, `services/room_service.py`.
- **B — harness & memory:** `harness/` (model client, context, adjudicator,
  narrator, memory, probes, evaluator, optimizer).
- **C — product & integration:** `api/`, `ui/`, `services/turn_orchestrator.py`,
  integration tests, scripts, demo.

## Rules for changes (TDD §32)

- Do not change a **[D]** rule; you may tune a **[DEF]** value in config (state
  the change + reason in the commit). **[OPEN]** items need a decision first.
- Every consequential state mutation produces an event; preserve turn
  idempotency; scope every query (incl. vector search) by `campaign_id`.
- Write the §20.1 unit tests alongside each domain module. Tests use
  `FakeModelClient` — never call real models in tests.

## Open items before the harness/memory layer

Embedding model + dimension, `MONGODB_URI`, `OPENROUTER_API_KEY`, per-role model
IDs (`MODEL_DRESSER`/`ADJUDICATOR`/`NARRATOR`/`VERIFIER`). See TDD §31.2.
