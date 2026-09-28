"""End-to-end browser tests (TDD §18).

A real Chromium renders the real page against a real uvicorn. The headless
DOM check in `tests/integration/ui_render_check.js` proves the render *logic*;
these prove the page actually works in a browser — event wiring, fetch, the
in-flight input lock, and that no script injected through narration ever runs.

The server runs on a random free port with its own state file, so a run never
touches the demo world. Skips cleanly when Playwright's browser is not
installed, so `pytest` stays green on a machine that has not run
`python -m playwright install chromium`.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STARTUP_TIMEOUT_S = 40


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(
    base_url: str, process: subprocess.Popen, log_path: Path
) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = log_path.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(f"server exited early:\n{output}")
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"server never became healthy: {last_error}")


@pytest.fixture(scope="session")
def browser_available(request) -> bool:
    """Whether a Playwright browser binary is actually installed."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            browser.close()
        return True
    except Exception:  # noqa: BLE001 - any launch failure means "not available"
        return False


@pytest.fixture(scope="session")
def live_server(tmp_path_factory) -> str:
    """A uvicorn on a random port, with a durable state file of its own."""
    port = _free_port()
    database_path = tmp_path_factory.mktemp("e2e-state") / "world.db"

    env = {
        **os.environ,
        "STUB_STATE_FILE": "",
        "SQLITE_DB_PATH": str(database_path),
        "DEBUG_ENDPOINTS": "true",
        "USE_FAKE_MODELS": "true",
        "PYTHONIOENCODING": "utf-8",
        # Never let a developer's real speech account be reached from a browser
        # test. pydantic-settings prefers this over .env.
        "ELEVENLABS_API_KEY": "",
    }
    # Log to a FILE, never to a pipe. uvicorn logs every request, and an
    # undrained subprocess pipe fills its OS buffer (~64 KB) and then blocks
    # the server forever — which looks exactly like a mysterious mid-suite
    # hang. Learned the hard way.
    log_path = database_path.parent / "uvicorn.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
            cwd=REPO_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        base_url = f"http://127.0.0.1:{port}"
        try:
            _wait_for_health(base_url, process, log_path)
            yield base_url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                process.kill()


@pytest.fixture
def page(browser_available, live_server, request):
    """A Chromium page, with console errors captured for assertions."""
    if not browser_available:
        pytest.skip("playwright browser not installed (python -m playwright install chromium)")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on(
            "console",
            lambda message: errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.console_errors = errors  # type: ignore[attr-defined]

        # Any dialog would block the session; record and dismiss rather than hang.
        dialogs: list[str] = []
        page.on(
            "dialog",
            lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()),
        )
        page.dialogs = dialogs  # type: ignore[attr-defined]

        try:
            yield page
        finally:
            context.close()
            browser.close()
