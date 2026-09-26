"""POST /api/speech. The provider is stubbed; tests never call ElevenLabs."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.services.speech import SpeechError, SpeechUnavailable


def _client(monkeypatch) -> TestClient:
    """The real app, without opening Atlas when this file is run on its own.

    Importing ``app.main`` builds the engine. If nothing else has imported it
    yet, force the in-memory seam first so a speech test does not need a
    database. A suite that already imported the app keeps that process-wide
    choice.
    """
    if "app.main" not in sys.modules:
        monkeypatch.setenv("USE_FAKE_MODELS", "true")
        monkeypatch.setenv("MONGODB_URI", "")
        from app.config import get_settings

        get_settings.cache_clear()
    import importlib

    return TestClient(importlib.import_module("app.main").app)


def test_speech_returns_mpeg(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes_speech.synthesize", lambda text: b"ID3-" + text.encode()
    )
    response = _client(monkeypatch).post(
        "/api/speech", json={"text": " You enter the hall. "}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.headers["cache-control"] == "no-store"
    assert response.content == b"ID3-You enter the hall."


def test_unconfigured_speech_is_503(monkeypatch):
    def unavailable(text: str) -> bytes:
        raise SpeechUnavailable("Speech is not configured.")

    monkeypatch.setattr("app.api.routes_speech.synthesize", unavailable)
    response = _client(monkeypatch).post("/api/speech", json={"text": "Hello."})

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "not configured" in body["error"]["message"]


def test_provider_failure_is_502(monkeypatch):
    def broken(text: str) -> bytes:
        raise SpeechError("ElevenLabs returned HTTP 401.")

    monkeypatch.setattr("app.api.routes_speech.synthesize", broken)
    response = _client(monkeypatch).post("/api/speech", json={"text": "Hello."})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "BAD_GATEWAY"


def test_blank_or_oversized_text_is_rejected(monkeypatch):
    client = _client(monkeypatch)
    blank = client.post("/api/speech", json={"text": "   "})
    huge = client.post("/api/speech", json={"text": "a" * 4001})

    assert blank.status_code == 422
    assert huge.status_code == 422


def test_client_asks_for_speech_without_html_sinks():
    source = (
        Path(__file__).resolve().parents[2] / "app" / "ui" / "static" / "app.js"
    ).read_text(encoding="utf-8")
    assert "/api/speech" in source
    assert "enabled: false" in source
    assert 'getItem(SPEECH_KEY) === "on"' in source
    assert "xi-api-key" not in source
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert forbidden not in source
