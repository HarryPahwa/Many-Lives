"""Image clients for room visuals (Room Visuals §9.1).

Uses ``urllib.request`` deliberately: ``httpx`` is a dev-only dependency here
and ``openai`` does not cover this endpoint, so the standard library avoids a
dependency and lockfile change for an optional feature.

The API key is read from settings, sent only in the Authorization header, and
never placed in an exception message, a log line, or a stored record (VIS-08).
"""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger("many_lives.visuals")

OPENROUTER_IMAGE_URL = "https://openrouter.ai/api/v1/images"

#: A response larger than this is refused rather than buffered (§9.1).
MAX_IMAGE_BYTES = 5 * 1024 * 1024

#: The smallest valid JPEG this repo needs: used by the fake client so tests
#: exercise real bytes without a network call.
_FAKE_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000"
    "ffdb004300ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffc00011080001000103012200021101031101"
    "ffc4001f0000010501010101010100000000000000000102030405060708090a0b"
    "ffda000c03010002110311003f00fefeffd9"
)


class ImageGenerationError(RuntimeError):
    """A failed image call, carrying a short machine-readable code.

    The code is what reaches the client and the stored record; the underlying
    provider text is deliberately not propagated, because it can echo the
    request (VIS-08).
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code


@dataclass
class ImageResult:
    data: bytes
    media_type: str = "image/jpeg"
    cost: float | None = None
    latency_ms: int = 0
    model: str = ""


class ImageClient(Protocol):
    def generate(self, prompt: str, *, references: list[bytes]) -> ImageResult: ...


@dataclass
class FakeImageClient:
    """Deterministic client for tests. Records what it was asked for."""

    fail_with: str | None = None
    delay_s: float = 0.0
    model: str = "fake/image"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def generate(self, prompt: str, *, references: list[bytes]) -> ImageResult:
        self.calls.append({"prompt": prompt, "references": len(references)})
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.fail_with:
            raise ImageGenerationError(self.fail_with)
        return ImageResult(
            data=_FAKE_JPEG,
            media_type="image/jpeg",
            cost=0.0,
            latency_ms=1,
            model=self.model,
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)


class OpenRouterImageClient:
    """Live client. One POST, JSON in, base64 image out."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        aspect_ratio: str,
        quality: str,
        output_compression: int,
        timeout_s: int,
        url: str = OPENROUTER_IMAGE_URL,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._aspect_ratio = aspect_ratio
        self._quality = quality
        self._compression = output_compression
        self._timeout = timeout_s
        self._url = url

    def generate(self, prompt: str, *, references: list[bytes]) -> ImageResult:
        body: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            # 16:9 is rejected by the default model; the accepted set differs
            # per model, which is why this is configuration (§3.1).
            "aspect_ratio": self._aspect_ratio,
            "quality": self._quality,
            "output_format": "jpeg",
            "output_compression": self._compression,
        }
        if references:
            body["input_references"] = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64,"
                        + base64.b64encode(reference).decode("ascii")
                    },
                }
                for reference in references
            ]

        request = urllib.request.Request(
            self._url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read(MAX_IMAGE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            # Never include the response body: it can echo the request, and the
            # request carries no secrets but the header does.
            code = "HTTP_4XX" if 400 <= exc.code < 500 else "HTTP_5XX"
            logger.warning("image call failed: %s (status %s)", code, exc.code)
            raise ImageGenerationError(code, f"status {exc.code}") from None
        except TimeoutError:
            logger.warning("image call failed: TIMEOUT")
            raise ImageGenerationError("TIMEOUT") from None
        except urllib.error.URLError as exc:
            reason = type(exc.reason).__name__ if exc.reason else "unknown"
            if "timeout" in reason.lower():
                raise ImageGenerationError("TIMEOUT") from None
            logger.warning("image call failed: NETWORK (%s)", reason)
            raise ImageGenerationError("NETWORK", reason) from None
        latency_ms = int((time.perf_counter() - started) * 1000)

        if len(raw) > MAX_IMAGE_BYTES:
            raise ImageGenerationError("TOO_LARGE")

        try:
            payload = json.loads(raw)
            entry = payload["data"][0]
            data = base64.b64decode(entry["b64_json"])
        except Exception:  # noqa: BLE001 - any shape problem is one failure mode
            raise ImageGenerationError("BAD_RESPONSE") from None

        if not data:
            raise ImageGenerationError("BAD_RESPONSE", "empty image")
        if len(data) > MAX_IMAGE_BYTES:
            raise ImageGenerationError("TOO_LARGE")

        usage = payload.get("usage") or {}
        cost = usage.get("cost")
        return ImageResult(
            data=data,
            media_type=entry.get("media_type") or "image/jpeg",
            cost=float(cost) if isinstance(cost, (int, float)) else None,
            latency_ms=latency_ms,
            model=payload.get("model") or self._model,
        )


# ---------------------------------------------------------------------------
# Selection (§9.1)
# ---------------------------------------------------------------------------

_CLIENT: ImageClient | None = None


def build_client() -> ImageClient:
    from app.config import get_settings

    settings = get_settings()
    choice = (settings.image_client or "auto").lower()
    if choice == "auto":
        choice = "fake" if settings.use_fake_models else "openrouter"

    if choice == "openrouter":
        if not settings.openrouter_api_key:
            # Surfacing this as a normal failure keeps the turn loop unaffected.
            raise ImageGenerationError("NO_API_KEY")
        return OpenRouterImageClient(
            settings.openrouter_api_key,
            model=settings.image_model,
            aspect_ratio=settings.image_aspect_ratio,
            quality=settings.image_quality,
            output_compression=settings.image_output_compression,
            timeout_s=settings.image_timeout_s,
        )
    return FakeImageClient()


def get_client() -> ImageClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = build_client()
    return _CLIENT


def set_client(client: ImageClient | None) -> None:
    """Test hook; None forces a rebuild on next use."""
    global _CLIENT
    _CLIENT = client
