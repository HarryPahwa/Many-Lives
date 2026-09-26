"""ElevenLabs speech client. No network: the poster is injected."""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message

import pytest

from app.config import Settings
from app.services.speech import SpeechError, SpeechUnavailable, synthesize

_SETTINGS = Settings(
    elevenlabs_api_key="test-key",
    elevenlabs_voice_id="voice123",
    elevenlabs_model_id="eleven_flash_v2_5",
)


def test_missing_key_does_not_call_the_provider():
    def post(url, data, headers, timeout):  # noqa: ARG001
        raise AssertionError("provider should not be called")

    with pytest.raises(SpeechUnavailable):
        synthesize("Hello.", settings=Settings(elevenlabs_api_key=""), post=post)


def test_synthesize_posts_text_model_and_key():
    captured: dict = {}

    def post(url, data, headers, timeout):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        captured["timeout"] = timeout
        return b"ID3fake-audio"

    audio = synthesize("  You enter the hall.  ", settings=_SETTINGS, post=post)

    assert audio == b"ID3fake-audio"
    assert captured["url"].endswith("/voice123?output_format=mp3_44100_128")
    assert captured["headers"]["xi-api-key"] == "test-key"
    assert captured["headers"]["Accept"] == "audio/mpeg"
    body = json.loads(captured["data"])
    assert body == {"text": "You enter the hall.", "model_id": "eleven_flash_v2_5"}
    assert captured["timeout"] > 0


def test_http_error_hides_the_api_key():
    secret = "super-secret-key"

    def post(url, data, headers, timeout):  # noqa: ARG001
        raise urllib.error.HTTPError(
            url,
            401,
            "Unauthorized",
            Message(),
            io.BytesIO(f"rejected {secret}".encode()),
        )

    settings = Settings(elevenlabs_api_key=secret, elevenlabs_voice_id="voice123")
    with pytest.raises(SpeechError) as caught:
        synthesize("Hello.", settings=settings, post=post)

    assert secret not in str(caught.value)
    assert "401" in str(caught.value)


def test_unreachable_provider_is_a_speech_error():
    def post(url, data, headers, timeout):  # noqa: ARG001
        raise urllib.error.URLError("timed out")

    with pytest.raises(SpeechError, match="Could not reach ElevenLabs"):
        synthesize("Hello.", settings=_SETTINGS, post=post)


def test_empty_audio_is_a_speech_error():
    def post(url, data, headers, timeout):  # noqa: ARG001
        return b""

    with pytest.raises(SpeechError, match="empty audio"):
        synthesize("Hello.", settings=_SETTINGS, post=post)
