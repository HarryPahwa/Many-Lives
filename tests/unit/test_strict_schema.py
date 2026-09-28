"""Tests for provider-compatible strict schema generation."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from app.domain.types import ActionProposal, RoomDressing
from app.harness.candidate_generator import ModelCandidateGenerationResult
from app.harness.strict_schema import SchemaReferenceError, strict_schema


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


@pytest.mark.parametrize("model", [RoomDressing, ActionProposal])
def test_schema_is_closed_reference_free_and_requires_all_properties(model):
    schema = strict_schema(model)
    removed = {
        "$ref",
        "default",
        "title",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "pattern",
    }

    for node in walk(schema):
        assert not (removed & set(node))
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert node["required"] == list(node["properties"])

    if model is RoomDressing:
        feature = schema["properties"]["features"]["items"]
        assert set(feature["properties"]) == {
            "slot_id",
            "kind",
            "name",
            "properties",
            "initial_state",
        }


def test_nullable_fields_keep_a_null_union_after_schema_normalization():
    schema = strict_schema(ActionProposal)
    utterance = schema["properties"]["utterance"]
    assert {entry.get("type") for entry in utterance["anyOf"]} == {"string", "null"}


def test_nested_discriminated_effects_preserve_the_discriminator():
    schema = strict_schema(ActionProposal)
    effects = schema["properties"]["proposed_effects_on_success"]["items"]
    assert effects["discriminator"]["propertyName"] == "type"
    assert "oneOf" in effects


def test_cyclic_local_reference_is_rejected():
    class RecursiveModel(BaseModel):
        child: RecursiveModel | None = None

    with pytest.raises(SchemaReferenceError, match="Cyclic"):
        strict_schema(RecursiveModel)


def test_removable_sibling_keywords_do_not_break_reference_inlining():
    schema = strict_schema(ModelCandidateGenerationResult)

    for node in walk(schema):
        assert "$ref" not in node
        assert "default" not in node

    bundle = schema["properties"]["candidates"]["items"]
    assert "execution" not in bundle["properties"]
    assert "origin" not in bundle["properties"]
