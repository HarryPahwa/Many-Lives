# CLAUDE.md

Claude Code reads this file automatically. The project rules live in AGENTS.md:

@AGENTS.md

## Specs
- Implementation spec (authoritative): docs/Agentic_Dungeon_Harness_TDD_v1_1.md
- Human summary: docs/Agentic Dungeon Harness — Design Summary v1.0.md

## Environment notes
- Dev machines include Windows (PowerShell). Prefer cross-platform Python; avoid bash-only scripts.
- Run: `uvicorn app.main:app --reload` · Test: `pytest`
- Never read, print, or commit `.env` values. The repo is public.
- Tests and local UI work use `USE_FAKE_MODELS=true`; never call real models in tests.

## Working agreement
- Stay inside your workstream's files (AGENTS.md "Workstreams"). If you need a change in another
  owner's module, add a stub/adapter in your own layer and note it for that owner.
- Small commits on a feature branch; pull/rebase from main often; keep `pytest` green.
