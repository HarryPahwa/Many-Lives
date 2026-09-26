"""The scripted play driver (§20.4) must stay green — and must be able to fail.

`scripts/play_script.py` is the post-merge smoke test (§32.2 item 7), so a
broken driver silently passing would be worse than no driver at all. These
tests drive its `run()` over a TestClient instead of a socket, so the suite
needs no live server.

Exit codes were verified against a real uvicorn this session: 0 on success,
1 on a failed check, 2 when the server is unreachable.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.stubs import reset_stubs

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "play_script.py"


def _load_script():
    """Import scripts/play_script.py, which is not on the package path.

    The module must be registered in sys.modules before it executes: it uses
    `from __future__ import annotations`, so @dataclass resolves its field
    types lazily by looking the module up by name.
    """
    if "play_script" in sys.modules:
        return sys.modules["play_script"]
    spec = importlib.util.spec_from_file_location("play_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["play_script"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _fresh_world():
    get_settings.cache_clear()
    reset_stubs()
    yield
    reset_stubs()


@pytest.fixture
def script():
    return _load_script()


@pytest.fixture
def runner(script):
    """A Runner whose HTTP calls go through TestClient rather than a socket."""
    client = TestClient(app)

    class InProcessRunner(script.Runner):
        def _request(self, method: str, path: str, body: dict | None = None):
            response = client.request(
                method, path, json=body if body is not None else None
            )
            if response.status_code >= 400:
                raise script.CheckFailed(
                    f"{method} {path} -> HTTP {response.status_code}: "
                    f"{json.dumps(response.json())}"
                )
            return response.json()

    return InProcessRunner(base_url="http://testserver")


def test_scripted_play_passes_every_check(script, runner):
    exit_code = script.run(runner, seed=9, campaign_id=None, demo=False)

    assert runner.failures == [], runner.failures
    assert exit_code == 0
    assert runner.checks_passed >= 20, "the driver should assert more than a smoke ping"


def test_driver_reports_failure_when_an_expectation_breaks(script, runner):
    """A driver that cannot go red is not a smoke test."""
    runner.check("deliberately false", False, "injected")
    exit_code = script.run(runner, seed=9, campaign_id=None, demo=False)

    assert exit_code == 1
    assert any("deliberately false" in f for f in runner.failures)


def test_driver_surfaces_an_unknown_campaign(script, runner):
    with pytest.raises(script.CheckFailed):
        script.run(runner, seed=9, campaign_id="cmp_doesnotexist", demo=False)


def test_demo_script_is_fast_path_only(script):
    """The demo must not depend on a model being reachable (§20.4)."""
    from app.services.stubs import get_engine

    engine = get_engine()
    for command in script.DEMO_SCRIPT:
        assert engine.parse_fast_path(command, "player_1") is not None, (
            f"'{command}' is not a fast-path command; the demo would need a model"
        )
