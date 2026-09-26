# Submission checklist (TDD §29.4)

Status at the time of writing. Items marked **human** need a person; the rest
were verified by running the thing named.

## §29.4 items

- [x] **Repository public.**
      `gh repo view` → `PUBLIC`, <https://github.com/HarryPahwa/Many-Lives>
- [x] **README states what was built during the event**, with an architecture
      summary, setup steps, and demo instructions (the last links to
      `docs/DEMO.md`).
- [x] **No secrets in the repository history.**
      Swept all 26 commits across every ref for connection strings and
      provider keys: no matches. `.env` has never been tracked, and
      `.gitignore` covers it plus the local stub state file.
- [ ] **Project built within the provided Atlas Sandbox cluster.** — **blocked**
      `MONGODB_URI` is still unset and no Atlas-backed engine is wired behind
      `get_engine()`. See "Honest status" below; this is the one item that is
      not merely paperwork.
- [ ] **1-minute video uploaded and linked.** — **human** (§29.3)
- [ ] **All team members added on the submission page.** — **human**
- [ ] **Demo link, if any, reachable.** — **human**; the demo is local, so
      there may be no link to give.

## Verification run before submitting

Run these; all four must pass.

```bash
pytest -m "not e2e"     # 162 passed, 5 skipped
pytest tests/e2e        # 14 passed (needs: playwright install chromium)
node tests/integration/ui_render_check.js
python scripts/play_script.py            # 22 checks, exit 0
python scripts/seed_stress_history.py    # P07, exit 0
```

The 5 skips are the §20.2 restart assertions, which are gated on an engine
reporting `DURABLE = True`. They are exercised for real against the
file-backed engine in `tests/integration/test_durable_stub.py`, so the paths
are covered; they light up on the Atlas engine when it lands.

## Honest status, for the submission text

Write this plainly rather than glossing it. Judges can read the repository.

**The measured claim:** `scripts/seed_stress_history.py` grows stored history
100x (100 to 10,000 events, 3.8 MB) against Developer B's real context
builder and shows the per-call context moving 239 to 246 tokens — 1.03x,
against a 3000-token budget. That is the Long Horizon evidence, and
`docs/p07_result.json` is the artefact. It is not a billion-token claim.

**What works and is tested:** the full turn lifecycle (§7.1) with per-campaign
locking and `turn_id` idempotency; campaign create/list/resume; a persistent
7×7 dungeon with lazy room generation that is never regenerated; fog-of-war
minimap, character and inventory panels; the context inspector over the
`turns` record; append-only turn records with per-call model metrics; the
§17.1 error contract; and restart recovery proven by killing the process.

**What is stubbed:** the engine and harness seams (`EnginePort` /
`HarnessPort` in `app/services/stubs.py`). Developer A's Atlas persistence and
Developer B's model calls swap in at one function each, without touching the
API, the orchestrator, or the UI.

**What is not claimed:** no Statement One / autonomous-learning claim, because
the automatic policy promotion loop has not run with before/after metrics
(§3.2, rule 13). No billion-token claim. If the demo runs on the file-backed
engine rather than Atlas, say so when asked.

## Where things are

| Thing | Path |
|---|---|
| Demo runbook and Q&A | `docs/DEMO.md` |
| Integration seam contract | `app/services/stubs.py`, issue #7 |
| Implementation spec | `docs/Agentic_Dungeon_Harness_TDD_v1_1.md` |
| Smoke test | `scripts/play_script.py` |
| UI screenshot | `docs/ui-screenshot.png` |
