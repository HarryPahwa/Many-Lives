"""FastAPI application shell (TDD §6, §17, §25).

Wires routers and serves the static UI. Startup hooks (index creation, policy
v1 seed, memory sweep) are registered later as those layers land.
Scaffold: app boots, routers included (currently empty), health check + UI.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import routes_campaigns, routes_debug, routes_evals, routes_turns

app = FastAPI(title="Agentic Dungeon Harness", version="0.1.0")

app.include_router(routes_campaigns.router)
app.include_router(routes_turns.router)
app.include_router(routes_debug.router)
app.include_router(routes_evals.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


_STATIC_DIR = Path(__file__).parent / "ui" / "static"
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="ui")
