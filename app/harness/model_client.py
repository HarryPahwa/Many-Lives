"""OpenRouter structured-output client and deterministic test double (TDD §10.1)."""

from __future__ import annotations

import hashlib
import logging
import random
import time
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings, get_settings
from app.domain.types import Role
from app.harness.strict_schema import strict_schema

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger("many_lives.model_client")


class StructuredResult(BaseModel, Generic[T]):
    """A parsed structured response with the provider data needed for turn records."""

    model_config = ConfigDict(extra="forbid")

    parsed: T
    usage: dict[str, int]
    latency_ms: int
    attempts: int
    model: str
    retry_errors: list[str] = Field(default_factory=list)


class ModelOutputError(RuntimeError):
    """A model response could not be safely used after bounded retries."""


class ModelClient(Protocol):
    def structured(
        self,
        role: Role,
        system: str,
        user: str,
        output_model: type[T],
        *,
        temperature: float,
        max_output_tokens: int,
        timeout_s: float,
    ) -> StructuredResult[T]: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


_ROLE_DEFAULTS: dict[Role, tuple[float, int, float]] = {
    Role.DRESSER: (0.8, 900, 20.0),
    Role.ADJUDICATOR: (0.2, 600, 20.0),
    Role.NARRATOR: (0.7, 500, 25.0),
    Role.VERIFIER: (0.0, 300, 20.0),
    Role.MEMORY_SUMMARIZER: (0.3, 120, 15.0),
}


def role_defaults(role: Role) -> tuple[float, int, float]:
    """Return the TDD default generation parameters for a model role."""

    return _ROLE_DEFAULTS[role]


class OpenRouterModelClient:
    """The production OpenRouter client; importing the SDK is deliberately lazy."""

    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        if client is not None:
            self._client = client
            return
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - depends on local installation
            raise RuntimeError("Install project dependencies to use OpenRouterModelClient") from exc
        self._client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.settings.openrouter_api_key,
        )

    def _model_for(self, role: Role) -> str:
        model = {
            Role.DRESSER: self.settings.model_dresser,
            Role.ADJUDICATOR: self.settings.model_adjudicator,
            Role.NARRATOR: self.settings.model_narrator,
            Role.VERIFIER: self.settings.model_verifier,
            Role.MEMORY_SUMMARIZER: self.settings.model_narrator,
        }[role]
        if not model:
            raise ModelOutputError(f"No model configured for role {role}")
        return model

    def structured(
        self,
        role: Role,
        system: str,
        user: str,
        output_model: type[T],
        *,
        temperature: float,
        max_output_tokens: int,
        timeout_s: float,
    ) -> StructuredResult[T]:
        model = self._model_for(role)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error: Exception | None = None
        retry_errors: list[str] = []
        call_started = time.monotonic()

        def record_failure(attempt: int, started: float, exc: Exception) -> None:
            elapsed_ms = round((time.monotonic() - started) * 1000)
            detail = " ".join(str(exc).split())[:300] or "no error detail"
            diagnostic = (
                f"attempt {attempt} after {elapsed_ms}ms: "
                f"{type(exc).__name__}: {detail}"
            )
            retry_errors.append(diagnostic)
            logger.warning(
                "structured model attempt failed role=%s model=%s attempt=%d "
                "elapsed_ms=%d error_type=%s error=%s",
                role.value,
                model,
                attempt,
                elapsed_ms,
                type(exc).__name__,
                detail,
            )

        for attempt in range(1, 4):
            started = time.monotonic()
            try:
                response = self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": output_model.__name__,
                            "strict": True,
                            "schema": strict_schema(output_model),
                        },
                    },
                    extra_body={"provider": {"require_parameters": True}},
                    temperature=temperature,
                    max_tokens=max_output_tokens,
                    timeout=timeout_s,
                )
                content = response.choices[0].message.content
                if not isinstance(content, str):
                    raise ModelOutputError("Provider returned an empty structured response")
                parsed = output_model.model_validate_json(content)
                usage = getattr(response, "usage", None)
                return StructuredResult(
                    parsed=parsed,
                    usage={
                        "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                        "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                    },
                    latency_ms=round((time.monotonic() - call_started) * 1000),
                    attempts=attempt,
                    model=model,
                    retry_errors=retry_errors,
                )
            except (ValidationError, ValueError, IndexError, ModelOutputError) as exc:
                last_error = exc
                record_failure(attempt, started, exc)
                if attempt == 3:
                    break
                messages.append(
                    {
                        "role": "user",
                        "content": f"Your previous JSON failed validation: {exc}. Return corrected JSON only.",
                    }
                )
            except Exception as exc:
                last_error = exc
                record_failure(attempt, started, exc)
                if attempt == 2:
                    break
                time.sleep(0.05 * attempt)

        error = ModelOutputError(
            f"Structured output failed for {role} after {attempt} attempts; "
            + " | ".join(retry_errors)
        )
        error.retry_errors = retry_errors
        error.latency_ms = round((time.monotonic() - call_started) * 1000)
        raise error from last_error

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.settings.embedding_model:
            raise ModelOutputError("No embedding model configured")
        response = self._client.embeddings.create(model=self.settings.embedding_model, input=texts)
        return [list(item.embedding) for item in response.data]


class FakeModelClient:
    """Deterministic fixture-backed model client for unit and integration tests."""

    def __init__(
        self,
        fixtures: dict[tuple[Role, str], BaseModel] | None = None,
        *,
        scenario: str = "default",
        embedding_dims: int = 1536,
        fail_on: set[Role] | None = None,
    ) -> None:
        self.fixtures = fixtures or {}
        self.scenario = scenario
        self.embedding_dims = embedding_dims
        self.fail_on = fail_on or set()
        self.calls: list[dict[str, Any]] = []

    def structured(
        self,
        role: Role,
        system: str,
        user: str,
        output_model: type[T],
        *,
        temperature: float,
        max_output_tokens: int,
        timeout_s: float,
    ) -> StructuredResult[T]:
        self.calls.append(
            {
                "role": role,
                "system": system,
                "user": user,
                "output_model": output_model,
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
                "timeout_s": timeout_s,
            }
        )
        if role in self.fail_on:
            raise ModelOutputError(f"Injected failure for role {role}")
        fixture = self.fixtures.get((role, self.scenario))
        if fixture is None:
            raise ModelOutputError(f"No fixture for role {role} and scenario {self.scenario!r}")
        parsed = output_model.model_validate(fixture.model_dump(mode="json"))
        return StructuredResult(
            parsed=parsed,
            usage={"input_tokens": 0, "output_tokens": 0},
            latency_ms=0,
            attempts=1,
            model="fake",
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embedding_for(text) for text in texts]

    def _embedding_for(self, text: str) -> list[float]:
        seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
        generator = random.Random(seed)
        return [generator.uniform(-1.0, 1.0) for _ in range(self.embedding_dims)]


def get_model_client(settings: Settings | None = None) -> ModelClient:
    """Build the configured model client without exposing credentials to callers."""

    resolved_settings = settings or get_settings()
    if resolved_settings.use_fake_models:
        return FakeModelClient(embedding_dims=resolved_settings.embedding_dims)
    return OpenRouterModelClient(resolved_settings)
