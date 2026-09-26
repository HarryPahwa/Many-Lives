"""ElevenLabs narration speech. The API key stays on the server.

A missing key or a provider failure must not affect the turn: callers turn
these exceptions into an HTTP error, and the page still shows the text.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import quote

from app.config import Settings, get_settings

_TIMEOUT_S = 20.0
Post = Callable[[str, bytes, dict[str, str], float], bytes]


class SpeechUnavailable(RuntimeError):
    """Speech is not configured. Narration stays text."""


class SpeechError(RuntimeError):
    """The provider failed. The message must not include the API key."""


def synthesize(
    text: str,
    *,
    settings: Settings | None = None,
    post: Post | None = None,
) -> bytes:
    """Return MP3 bytes for `text`."""
    settings = settings or get_settings()
    api_key = settings.elevenlabs_api_key.strip()
    if not api_key:
        raise SpeechUnavailable("Speech is not configured.")

    voice_id = settings.elevenlabs_voice_id.strip()
    model_id = settings.elevenlabs_model_id.strip()
    if not voice_id or not model_id:
        raise SpeechUnavailable("Speech is not configured.")

    spoken = text.strip()
    if not spoken:
        raise SpeechError("Nothing to speak.")

    url = (
        "https://api.elevenlabs.io/v1/text-to-speech/"
        f"{quote(voice_id, safe='')}?output_format=mp3_44100_128"
    )
    payload = json.dumps({"text": spoken, "model_id": model_id}).encode("utf-8")
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    sender = post or _post
    try:
        audio = sender(url, payload, headers, _TIMEOUT_S)
    except urllib.error.HTTPError as exc:
        raise SpeechError(f"ElevenLabs returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise SpeechError("Could not reach ElevenLabs.") from exc

    if not audio:
        raise SpeechError("ElevenLabs returned empty audio.")
    return audio


def _post(url: str, data: bytes, headers: dict[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()
