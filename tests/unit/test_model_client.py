"""Unit tests for provider isolation, retries, and fake-model determinism."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.domain.types import (
    EntityDressing,
    FeatureDressing,
    ItemDressing,
    Role,
    RoomDressing,
    StaticEnvironment,
)
from app.harness.model_client import (
    FakeModelClient,
    ModelOutputError,
    OpenRouterModelClient,
    get_model_client,
    role_defaults,
)


def fixture_dressing() -> RoomDressing:
    return RoomDressing(
        room_name="Moss Crypt",
        static_environment=StaticEnvironment(
            materials=["stone"],
            lighting="dim",
            smell="earth",
            architectural_notes="arched ceiling",
        ),
        features=[
            FeatureDressing(
                slot_id="feature_1",
                kind="chair",
                name="rotting chair",
                properties=["movable"],
                initial_state={"orientation": "upright"},
            ),
            FeatureDressing(
                slot_id=None,
                kind="sconce",
                name="cold sconce",
                properties=["light_source"],
                initial_state={"light_state": "unlit"},
            ),
        ],
        entities=[
            EntityDressing(
                slot_id="npc_1",
                name="Mara",
                description="a wary archivist",
                persona="guarded",
                traits=["watchful"],
            )
        ],
        items=[ItemDressing(slot_id="item_1", name="brass key", description="a dull key")],
    )


def structured_call(client):
    return client.structured(
        Role.DRESSER,
        "system",
        "user",
        RoomDressing,
        temperature=0.8,
        max_output_tokens=900,
        timeout_s=20,
    )


def response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
    )


class SequenceProvider:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def settings() -> Settings:
    return Settings(
        model_dresser="provider/dresser",
        model_adjudicator="provider/adjudicator",
        model_narrator="provider/narrator",
        model_verifier="provider/verifier",
        embedding_model="provider/embed",
        embedding_dims=8,
    )


def test_fake_client_returns_validated_fixture_records_call_and_is_deterministic():
    fixture = fixture_dressing()
    first = FakeModelClient({(Role.DRESSER, "default"): fixture}, embedding_dims=8)
    second = FakeModelClient({(Role.DRESSER, "default"): fixture}, embedding_dims=8)

    result = structured_call(first)

    assert result.parsed == fixture
    assert result.model == "fake"
    assert first.calls[0]["role"] == Role.DRESSER
    assert first.embed(["Mara remembers the key."]) == second.embed(["Mara remembers the key."])
    assert len(first.embed(["text"])[0]) == 8


def test_fake_client_can_inject_a_role_failure():
    client = FakeModelClient(fail_on={Role.DRESSER})

    with pytest.raises(ModelOutputError, match="Injected failure"):
        structured_call(client)


def test_provider_retries_invalid_json_then_returns_parsed_result():
    dressing = fixture_dressing()
    provider = SequenceProvider(
        [
            response("not json"),
            response("{\"room_name\": \"missing required fields\"}"),
            response(dressing.model_dump_json()),
        ]
    )
    client = OpenRouterModelClient(settings(), client=provider)

    result = structured_call(client)

    assert result.parsed == dressing
    assert result.attempts == 3
    assert len(provider.calls) == 3
    assert "failed validation" in provider.calls[1]["messages"][-1]["content"]
    request = provider.calls[0]
    assert request["extra_body"] == {"provider": {"require_parameters": True}}
    assert request["response_format"]["json_schema"]["strict"] is True
    assert "$ref" not in str(request["response_format"]["json_schema"]["schema"])


def test_provider_retries_a_transport_failure_once_then_succeeds():
    dressing = fixture_dressing()
    provider = SequenceProvider([RuntimeError("temporary provider failure"), response(dressing.model_dump_json())])
    client = OpenRouterModelClient(settings(), client=provider)

    result = structured_call(client)

    assert result.attempts == 2
    assert len(provider.calls) == 2


def test_provider_raises_after_exhausting_invalid_output_retries():
    provider = SequenceProvider([response("not json"), response("not json"), response("not json")])
    client = OpenRouterModelClient(settings(), client=provider)

    with pytest.raises(ModelOutputError, match="after 3 attempts"):
        structured_call(client)


def test_factory_uses_fake_models_and_role_defaults_match_the_tdd():
    client = get_model_client(Settings(use_fake_models=True, embedding_dims=4))

    assert isinstance(client, FakeModelClient)
    assert role_defaults(Role.NARRATOR) == (0.7, 500, 25.0)
