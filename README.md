# Many-Lives — Agentic Dungeon Harness

A persistent-world agent harness demonstrated by a turn-based, text-first
dungeon raid. The **harness is the product**; the game is the environment.

**Central invariant:** models may interpret, propose, describe, and reason;
only deterministic application code may establish or mutate canonical game
state.

See the design docs for the full specification:

- `docs/Agentic Dungeon Harness — Design Summary v1.0.md`
- `docs/Agentic_Dungeon_Harness_TDD_v1_1.md` — the implementation spec

## Status

Project **scaffold**. The repository structure (TDD §25), tooling, and the
FastAPI app shell are in place; game logic is not implemented yet. Each module
carries a docstring naming its responsibility and the TDD section it will
implement.

## Setup

Requires Python 3.12.

```bash
# create the env (conda shown; venv works too)
conda create -y -n many-lives python=3.12
conda activate many-lives

# install
pip install -e ".[dev]"

# configure (never commit .env)
cp .env.example .env   # then fill in MONGODB_URI, OPENROUTER_API_KEY, model ids

# run tests
pytest

# run the app shell
uvicorn app.main:app --reload   # http://127.0.0.1:8000 (health at /health)
```

## Layout (TDD §6.2, §25)

```
app/
  domain/       # A — pure rules engine, types, RNG (no I/O)
  world/        # A — topology, placement, room planning, fallback
  harness/      # B — model client, context, adjudicator, narrator, memory, eval
  persistence/  # A — Mongo client, repositories, transactions, indexes
  services/     # campaign / room services, turn orchestrator (integration seam)
  api/          # C — FastAPI routers and schemas
  ui/static/    # C — plain HTML/CSS/JS
config/         # balance.yaml (tuning constants)
scripts/        # indexes, stress history, probe suite, scripted play
tests/          # unit / integration / probes
```

**Dependency rule:** `domain` imports nothing from `harness`, `persistence`,
or `api`; `harness` may import `domain` types; `services` composes all layers.

## Workstreams

Three layers, integrated against shared contracts (TDD §27): **A** engine &
persistence, **B** harness & memory, **C** product & integration.
