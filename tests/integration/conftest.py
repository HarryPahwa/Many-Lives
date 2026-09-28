"""Integration-test harness (TDD §20.2).

All integration tests run against the process-local SQLite path installed by
the root conftest. No network service or credentials are used.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "durable: requires an engine whose state survives a restart"
    )


@pytest.fixture
def engine_is_durable() -> bool:
    """Whether the installed engine keeps state across a process restart."""
    import importlib

    # Resolved dynamically: a restart test may have reloaded the module.
    stubs = importlib.import_module("app.services.stubs")
    return bool(getattr(stubs.get_engine(), "DURABLE", False))


@pytest.fixture
def require_durable(engine_is_durable: bool) -> None:
    """Skip a restart test while the seam is backed by the in-memory stub."""
    if not engine_is_durable:
        pytest.skip(
            "installed engine is not durable (in-memory stub); "
            "needs Developer A's persistence behind get_engine()"
        )
