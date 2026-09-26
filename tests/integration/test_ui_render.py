"""Runs the headless UI render check (§18) as part of the Python suite.

`ui_render_check.js` evaluates `app/ui/static/app.js` against a minimal DOM
stub and asserts what the API tests cannot see: walls derived from exits, the
north = +y row order, the player and boss markers, and that markup in room
text lands as literal text. Skips cleanly where node is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

CHECK = Path(__file__).with_name("ui_render_check.js")
APP_JS = Path(__file__).resolve().parents[2] / "app" / "ui" / "static" / "app.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_app_js_parses():
    result = subprocess.run(
        ["node", "--check", str(APP_JS)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_minimap_and_panels_render_correctly():
    result = subprocess.run(
        ["node", str(CHECK)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all assertions passed" in result.stdout
