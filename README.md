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

The **end-to-end loop runs**: create a campaign, explore a persistent 7x7
dungeon from the browser, take items, fight, and resume after the server is
killed. Everything was written during the event (26 Sep 2026).

The integration layer (Developer C) talks to the engine and harness only
through the two protocols in `app/services/stubs.py`, so Developer A's Atlas
persistence and Developer B's model calls swap in at one place
(`get_engine()` / `get_harness()`) without touching the API, the orchestrator,
or the UI.

| Layer | State |
|---|---|
| API, turn orchestrator, UI, minimap, character panel, context inspector | working |
| Idempotent turns, fog of war, campaign resume, restart recovery | working |
| Scripted play driver / smoke test (`scripts/play_script.py`) | working |
| Engine and persistence (A) | Atlas engine behind `get_engine()` when `MONGODB_URI` is set; durable file store otherwise |
| Model calls and semantic memory (B) | real harness behind `get_harness()` when `USE_FAKE_MODELS=false` |
| Room visuals (optional) | working; off by default |
| Spoken narration (optional) | working; needs `ELEVENLABS_API_KEY` |

## Setup

Requires Python 3.12 or newer and, for the browser tests, Node.

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate elsewhere
pip install -e ".[dev]"

cp .env.example .env            # never commit .env; it is git-ignored

pytest -m "not e2e"             # no database, no model, no credentials needed
```

Then start it:

```powershell
.\scripts\run_local.ps1         # Windows (recommended)
```

```bash
STUB_STATE_FILE=.state/demo.json DEBUG_ENDPOINTS=true uvicorn app.main:app
```

Open **http://127.0.0.1:8000** and click *New campaign*.

Nothing above needs credentials. With `USE_FAKE_MODELS=true`, the app uses
deterministic fake candidate generation, JEV scoring, and narration while
retaining SQLite as canonical persistence.

### Why there is a run script

`STUB_STATE_FILE` is read with `os.getenv()` in `app/services/stubs.py`, not
through pydantic `Settings`, so **putting it in `.env` has no effect**. Without
it the engine is in-memory and `resume` returns `404 Unknown campaign` after a
restart — correct behaviour for a non-durable engine, but it looks exactly like
a bug. `scripts/run_local.ps1` sets it as a real environment variable, frees the
port if something is still listening, and prints where state and images go.

```powershell
.\scripts\run_local.ps1                 # durable state, visuals on
.\scripts\run_local.ps1 -Fresh          # wipe state and images first
.\scripts\run_local.ps1 -FakeModels     # no provider calls, no spend
.\scripts\run_local.ps1 -NoVisuals      # text only
.\scripts\run_local.ps1 -Port 8001      # somewhere else
```

### Configuration

Everything lives in `.env` (see `.env.example` for the full list of names).
The settings that change which code actually runs:

| Variable | Effect |
|---|---|
| `USE_FAKE_MODELS` | `false` enables the production candidate generator, OpenRouter JEV Decisions client, and narrator. `true` uses deterministic local fakes. |
| `DEBUG_ENDPOINTS` | `true` enables the context inspector. With `false`, `/debug/context` returns 404 by design (§22) and the UI hides the panel. |
| `SQLITE_DB_PATH` | Canonical SQLite database path; tests override it with isolated temporary databases. |
| `MODEL_JEV` | Decisions API model, default `typesafe/jev-1.13`. |
| `JEV_ENDPOINT` | OpenRouter Decisions endpoint, default `https://openrouter.ai/api/alpha/decisions`. |
| `STUB_STATE_FILE` | Optional legacy JSON compatibility seam. **Must be a real environment variable**, not a `.env` entry. |
| `ENABLE_ROOM_VISUALS` | The room illustrations. See below. |
| `ELEVENLABS_API_KEY` | Optional spoken narration. Blank keeps narration text-only. |

Verified model configuration — these exact ids were exercised against the live
provider:

```
MODEL_DRESSER=google/gemini-2.5-flash-lite
MODEL_ADJUDICATOR=google/gemini-2.5-flash-lite
MODEL_JEV=typesafe/jev-1.13
MODEL_NARRATOR=google/gemini-2.5-flash-lite
MODEL_VERIFIER=google/gemini-2.5-flash-lite
EMBEDDING_MODEL=perplexity/pplx-embed-v1-0.6b
EMBEDDING_DIMS=1024      # confirmed: this model returns 1024 dimensions
```

`EMBEDDING_DIMS` must match the configured embedding model. A mismatch fails
when stored and query vectors are compared.

## Room visuals (optional)

Each room can carry an illustration generated from its committed state. The
picture is a **read-only projection**, exactly as the narration is: nothing in
the visuals path writes campaign, cell, entity, event, memory or turn data, and
no image is ever read back to decide anything.

Full design: `docs/Room_Visuals_TDD.md`.

### Turning it ON

In `.env`:

```
ENABLE_ROOM_VISUALS=true
IMAGE_CLIENT=openrouter          # or `fake` for a free, offline placeholder
AUTO_UPDATE_ROOM_VISUALS=true    # render by itself; false = press the button
```

Then restart, or use `.\scripts\run_local.ps1` (visuals on by default).

With the flag on an image appears **by itself** when you enter a room that has
none, and updates by itself when something the picture can show changes — a
character crossing a health band or dying, a feature changing state, an item
appearing or leaving.

### Turning it OFF

```
ENABLE_ROOM_VISUALS=false
```

then restart, or run `.\scripts\run_local.ps1 -NoVisuals`.

Off is the default, and off means genuinely inert: every visual route returns
404, the panel never appears, the client makes no visual request at all, and the
rest of the app behaves exactly as if the feature did not exist.

To keep visuals but render only on demand, set `AUTO_UPDATE_ROOM_VISUALS=false`;
the panel then shows a *Generate visual* / *Update visual* button.

### What it costs

Roughly **$0.003 per new room** and **$0.007 per update**, 8–10 seconds each.
Exploring ten rooms is about three cents. Nothing is charged for revisiting a
room whose picture is still accurate: the stored image is served and no model is
called. A failed render is never retried automatically, so a broken provider
cannot bill you on every turn.

Fast-path commands (`north`, `look`, `take …`) never reach any model. Only free
text does.

### Where the images are stored

With `VISUAL_STORE=file` (the default when `STUB_STATE_FILE` is set):

```
.visuals/index.json                            records and metadata
.visuals/<campaign_id>/<asset_id>.img          the JPEG bytes
.state/demo.json                               the world itself
```

Both directories are git-ignored. `VISUAL_STORE=sqlite` puts the same records in
the `room_visuals` and `visual_assets` tables; `memory` keeps them only for the
life of the process.

Images survive a restart in the file and SQLite stores: resume a campaign and the
same asset id and the same bytes come back, with no regeneration.

## Verifying it works

```bash
pytest -m "not e2e"                        # unit + integration
pytest tests/e2e                           # real Chromium
node tests/integration/ui_render_check.js  # headless DOM checks
node tests/integration/ui_visual_check.js
python scripts/play_script.py              # 22 checks; exits 0/1/2
python scripts/seed_stress_history.py      # P07 bounded-context evidence
```

Tests never call a real provider. `tests/conftest.py` pins them to fakes even
when `.env` is configured for a live demo — without it, a `.env` carrying
`USE_FAKE_MODELS=false` turned a 17-second suite into six minutes of billed
calls. `ALLOW_REAL_MODELS_IN_TESTS=1` is the deliberate escape hatch.

To verify the separate OpenRouter JEV Decisions API integration explicitly,
configure `OPENROUTER_API_KEY` and run:

```bash
uv run python scripts/smoke_jev.py
```

This is opt-in and billable. Normal tests never execute it.

### Persistence across a restart

The central claim is that continuity comes from stored state, not a transcript.
Prove it rather than trusting it:

```powershell
.\scripts\run_local.ps1
python scripts/play_script.py --seed 9 --demo   # note the campaign id

Stop-Process -Id (Get-NetTCPConnection -LocalPort 8000 -State Listen).OwningProcess -Force
.\scripts\run_local.ps1                          # a brand new process
```

Then resume that campaign in the browser, or call
`POST /api/campaigns/<ID>/resume`. Room, cell, turn number, inventory and the
room's image must all be identical to before the kill.

In the browser a reload reconnects by itself: the page stores only the campaign
id, never game state.

`pkill` does not exist in Git Bash on Windows — use the PowerShell line above.

### Browser tests

```bash
python -m playwright install chromium   # once
pytest tests/e2e                        # 14 tests in a real Chromium
```

They start their own server on a free port with their own state file, and skip
rather than fail when the browser binary is absent.

![The harness running](docs/ui-screenshot.png)

### Demo

`docs/DEMO.md` is the rehearsed runbook: the live sequence with what to say, the
kill/restart/resume step, likely questions answered from the build, and a
troubleshooting table. `docs/SUBMISSION.md` is the §29.4 checklist.

### Smoke test

```bash
python scripts/play_script.py            # 22 checks; exits 0/1/2
```

Per TDD §32.2 item 7 this runs after every merge; a failure blocks further
merges until it is fixed.

### A note on binding

The server listens on `127.0.0.1` only. Do not add `--host 0.0.0.0` on shared
or venue wifi: there is no authentication anywhere in this app (§28.2 puts it
out of scope) and `DEBUG_ENDPOINTS=true` exposes internal state.

## Bounded context: the measured result (P07)

The Long Horizon claim is that stored history can grow without the per-call
context growing with it. `scripts/seed_stress_history.py` measures exactly
that against Developer B's real context builder:

| Stored events | Memories | Stored | Context tokens | Build |
|---:|---:|---:|---:|---:|
| 0 | 0 | 0 KB | 117 | 0.04 ms |
| 100 | 10 | 37 KB | 239 | 0.07 ms |
| 1,000 | 100 | 380 KB | 243 | 0.24 ms |
| 5,000 | 500 | 1,921 KB | 246 | 0.88 ms |
| 10,000 | 1,000 | 3,850 KB | 246 | 1.64 ms |

Stored history grew **100x** (100 to 10,000 events) while the context assembled for one model
call grew **1.03x**, peaking at **246 tokens of a 3000-token budget**.

The rise from an empty history to the first checkpoint is the policy's
recent-event window filling once; it is excluded from the ratio and printed
separately, because counting it as growth would describe the system wrongly.

Reproduce it:

```bash
python scripts/seed_stress_history.py     # writes docs/p07_result.json
```

This shows bounded context under a large stored history. It is **not** a claim
about a billion-token live campaign (TDD §16.4, §3.2).

## Layout (TDD §6.2, §25)

```
app/
  domain/       # A — pure rules engine, types, RNG (no I/O)
  world/        # A — topology, placement, room planning, fallback
  harness/      # B — model client, context, adjudicator, narrator, memory, eval
  persistence/  # A — SQLite lifecycle, repositories, transactions, indexes
  services/     # campaign / room services, turn orchestrator (integration seam)
  api/          # C — FastAPI routers and schemas
  ui/static/    # C — plain HTML/CSS/JS
config/         # world-generation and runtime-rule tuning constants
scripts/        # indexes, stress history, probe suite, scripted play
tests/          # unit / integration / probes
```

**Dependency rule:** `domain` imports nothing from `harness`, `persistence`,
or `api`; `harness` may import `domain` types; `services` composes all layers.

## The integration seam

`app/services/stubs.py` defines `EnginePort` (Developer A) and `HarnessPort`
(Developer B) and is the single place an implementation is chosen. The turn
orchestrator and every route talk to the protocols only, so swapping in real
code changes nothing else. See issue #7 for the contract and its gotchas.

## Central invariant, enforced

Models may interpret, propose, describe and reason; only deterministic code
establishes or mutates state. Concretely, and covered by tests:

- player text is an *attempted action*, never an authority — prompt-injection
  attempts change no state;
- the adjudicator returns a typed proposal that the engine re-validates;
- dice come from seeded code RNG, never from a model;
- a repeated `turn_id` returns the stored result and applies nothing;
- narration is produced *after* commit and can never alter state; if the
  narrator fails, the turn stays committed and falls back to template text;
- every model- or player-authored string reaches the page via `textContent`.

## What was built during the event

All of it. The repository began on 26 Sep 2026 with an empty scaffold; every
line of application code, every test, the prompts, the schemas and the demo
data were written inside the hacking window. `docs/SUBMISSION.md` records the
§29.4 checklist and an honest account of what works, what is stubbed, and what
is deliberately not claimed.

## Workstreams

Three layers, integrated against shared contracts (TDD §27): **A** engine &
persistence, **B** harness & memory, **C** product & integration.
