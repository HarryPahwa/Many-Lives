"""Room Visuals §14.1 item 5 — the OpenRouter client, with urlopen patched.

No live calls here. The most important assertion in the file is the last group:
the API key must never reach an exception string or a log record (VIS-08), and
this repository is public.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import urllib.error

import pytest

from app.services.image_client import (
    MAX_IMAGE_BYTES,
    FakeImageClient,
    ImageGenerationError,
    OpenRouterImageClient,
)

SECRET = "sk-or-v1-NEVERLEAKTHISVALUEANYWHERE0000000000000000000000000000000000"
IMAGE = b"\xff\xd8\xff\xdbfake-jpeg-bytes"


def client(**overrides) -> OpenRouterImageClient:
    kwargs = dict(
        model="openai/gpt-image-1-mini",
        aspect_ratio="3:2",
        quality="low",
        output_compression=70,
        timeout_s=90,
    )
    kwargs.update(overrides)
    return OpenRouterImageClient(SECRET, **kwargs)


class _Response:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, *_args):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def ok_payload(cost=0.0033) -> bytes:
    return json.dumps(
        {
            "created": 1,
            "model": "openai/gpt-image-1-mini",
            "data": [
                {
                    "b64_json": base64.b64encode(IMAGE).decode("ascii"),
                    "media_type": "image/jpeg",
                }
            ],
            "usage": {"cost": cost},
        }
    ).encode()


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


def test_the_request_matches_the_verified_api_shape(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return _Response(ok_payload())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = client().generate("a room", references=[])

    assert captured["url"] == "https://openrouter.ai/api/v1/images"
    assert captured["method"] == "POST"
    assert captured["timeout"] == 90
    body = captured["body"]
    assert body["model"] == "openai/gpt-image-1-mini"
    # 16:9 is rejected by this model; 3:2 is the verified value (§3.1).
    assert body["aspect_ratio"] == "3:2"
    assert body["output_format"] == "jpeg"
    assert body["output_compression"] == 70
    assert body["quality"] == "low"
    assert "input_references" not in body, "a generate must send no references"
    assert result.data == IMAGE
    assert result.cost == pytest.approx(0.0033)
    assert result.media_type == "image/jpeg"


def test_references_are_sent_as_data_urls(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data)
        return _Response(ok_payload())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client().generate("edit it", references=[IMAGE])

    references = captured["body"]["input_references"]
    assert len(references) == 1
    assert references[0]["type"] == "image_url"
    url = references[0]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == IMAGE


def test_the_configured_aspect_ratio_is_used(monkeypatch):
    """A different model may accept a different set (§3.1)."""
    captured = {}
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=None: (
            captured.update(body=json.loads(request.data)),
            _Response(ok_payload()),
        )[1],
    )
    client(aspect_ratio="1:1").generate("x", references=[])
    assert captured["body"]["aspect_ratio"] == "1:1"


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status,expected", [(400, "HTTP_4XX"), (429, "HTTP_4XX"), (500, "HTTP_5XX")])
def test_http_errors_map_to_codes(monkeypatch, status, expected):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, status, "boom", {}, io.BytesIO(b'{"error":"detail"}')
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ImageGenerationError) as exc:
        client().generate("x", references=[])
    assert exc.value.code == expected


def test_a_timeout_maps_to_TIMEOUT(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ImageGenerationError) as exc:
        client().generate("x", references=[])
    assert exc.value.code == "TIMEOUT"


@pytest.mark.parametrize(
    "payload",
    [
        b"not json at all",
        b"{}",
        b'{"data": []}',
        b'{"data": [{}]}',
        b'{"data": [{"b64_json": "!!!not base64!!!"}]}',
    ],
)
def test_malformed_responses_map_to_BAD_RESPONSE(monkeypatch, payload):
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda request, timeout=None: _Response(payload)
    )
    with pytest.raises(ImageGenerationError) as exc:
        client().generate("x", references=[])
    assert exc.value.code == "BAD_RESPONSE"


def test_an_oversized_response_is_refused(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=None: _Response(b"x" * (MAX_IMAGE_BYTES + 10)),
    )
    with pytest.raises(ImageGenerationError) as exc:
        client().generate("x", references=[])
    assert exc.value.code == "TOO_LARGE"


def test_an_empty_image_is_refused(monkeypatch):
    payload = json.dumps(
        {"data": [{"b64_json": "", "media_type": "image/jpeg"}]}
    ).encode()
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda request, timeout=None: _Response(payload)
    )
    with pytest.raises(ImageGenerationError) as exc:
        client().generate("x", references=[])
    assert exc.value.code == "BAD_RESPONSE"


# ---------------------------------------------------------------------------
# VIS-08 — the key must not leak. This repository is public.
# ---------------------------------------------------------------------------


def test_the_key_is_sent_only_in_the_authorization_header(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=None: (
            captured.update(headers=dict(request.headers), body=request.data),
            _Response(ok_payload()),
        )[1],
    )
    client().generate("a room", references=[])

    joined = json.dumps(captured["headers"])
    assert SECRET in joined, "the key must actually be sent"
    assert SECRET not in captured["body"].decode(), "never in the request body"


@pytest.mark.parametrize(
    "raiser",
    [
        lambda request, timeout=None: (_ for _ in ()).throw(
            urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"bad key"))
        ),
        lambda request, timeout=None: (_ for _ in ()).throw(TimeoutError()),
        lambda request, timeout=None: _Response(b"garbage"),
    ],
)
def test_the_key_never_appears_in_an_exception(monkeypatch, raiser):
    monkeypatch.setattr("urllib.request.urlopen", raiser)
    with pytest.raises(ImageGenerationError) as exc:
        client().generate("x", references=[])
    assert SECRET not in str(exc.value)
    assert "sk-or-" not in str(exc.value)
    assert SECRET not in repr(exc.value)


def test_the_key_never_appears_in_a_log_record(monkeypatch, caplog):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"bad key")
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ImageGenerationError):
            client().generate("x", references=[])
    assert "sk-or-" not in caplog.text
    assert SECRET not in caplog.text


# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------


def test_the_fake_returns_real_jpeg_bytes_and_counts_calls():
    fake = FakeImageClient()
    result = fake.generate("a room", references=[])
    assert result.data.startswith(b"\xff\xd8\xff"), "must look like a JPEG"
    assert fake.call_count == 1
    fake.generate("again", references=[IMAGE])
    assert fake.call_count == 2
    assert fake.calls[-1]["references"] == 1


def test_the_fake_can_fail_on_demand():
    fake = FakeImageClient(fail_with="HTTP_5XX")
    with pytest.raises(ImageGenerationError) as exc:
        fake.generate("x", references=[])
    assert exc.value.code == "HTTP_5XX"
