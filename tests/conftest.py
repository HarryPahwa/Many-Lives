"""Test-wide safety net: never call a real provider (TDD §32.2 item 4).

`Settings` reads `.env`, and a developer running the app for real will have
`USE_FAKE_MODELS=false` in there. Pydantic gives environment variables higher
precedence than the `.env` file, so setting them here — before any test
imports `app.config` — pins the whole suite to fakes regardless of what `.env`
says.

Without this the suite silently switches to the live provider: it went from
17 seconds and 465 passing to 6 minutes, 3 failures and real spend, purely
because `.env` had been configured for a live demo.

A developer who genuinely wants to exercise the live path can set
`ALLOW_REAL_MODELS_IN_TESTS=1`, which is deliberately awkward to type by
accident.
"""

from __future__ import annotations

import os

if os.environ.get("ALLOW_REAL_MODELS_IN_TESTS") != "1":
    os.environ["USE_FAKE_MODELS"] = "true"
    os.environ["IMAGE_CLIENT"] = "fake"
    # Never let a test reach a real cluster either: the integration harness
    # treats an empty URI as "run against the in-memory seam".
    os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_live_providers():
    """Fail loudly if a test ever constructs a live client by accident."""
    from app.config import get_settings

    if os.environ.get("ALLOW_REAL_MODELS_IN_TESTS") == "1":
        yield
        return

    settings = get_settings()
    assert settings.use_fake_models is True, (
        "tests must run with fake models (TDD §32.2 item 4); "
        "USE_FAKE_MODELS resolved to false"
    )
    yield
