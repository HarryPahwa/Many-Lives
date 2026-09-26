"""FastAPI application shell (TDD §6, §17, §25).

Wires routers, installs the §17.1 error envelope, and serves the static UI.
Startup hooks (index creation, policy v1 seed, memory sweep) are registered
here as those layers land.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import routes_campaigns, routes_debug, routes_evals, routes_turns

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Agentic Dungeon Harness", version="0.1.0")

# §17.1: every error is `{"error": {"code": "...", "message": "..."}}`.
_CODES = {
    400: "BAD_REQUEST",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "UNPROCESSABLE_ENTITY",
    503: "SERVICE_UNAVAILABLE",
}


def _error(status: int, message: str, code: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code or _CODES.get(status, "ERROR"),
                "message": message,
            }
        },
    )


@app.exception_handler(HTTPException)
async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return _error(exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Report the field and reason without echoing the submitted value: player
    # input is untrusted and must not be reflected back verbatim (§22).
    first = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
    message = first.get("msg", "Invalid request.")
    detail = f"{location}: {message}" if location else message
    return _error(422, detail, code="VALIDATION_ERROR")


app.include_router(routes_campaigns.router)
app.include_router(routes_turns.router)
app.include_router(routes_debug.router)
app.include_router(routes_evals.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Mounted last so /api and /health match first.
_STATIC_DIR = Path(__file__).parent / "ui" / "static"
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="ui")
