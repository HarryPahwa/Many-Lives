"""Provider-compatible strict JSON Schema generation (TDD §10.1)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel


_REMOVED_KEYWORDS = {
    "default",
    "title",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "minItems",
    "maxItems",
    "minProperties",
    "maxProperties",
}


class SchemaReferenceError(ValueError):
    """Raised when a local JSON Schema reference cannot be safely inlined."""


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return a closed, reference-free schema accepted by strict providers."""

    schema = deepcopy(model.model_json_schema())
    definitions = schema.pop("$defs", {})

    def resolve_reference(reference: str, seen: frozenset[str]) -> dict[str, Any]:
        prefix = "#/$defs/"
        if not reference.startswith(prefix):
            raise SchemaReferenceError(f"Only local $defs references are supported: {reference}")
        name = reference.removeprefix(prefix)
        if name not in definitions:
            raise SchemaReferenceError(f"Unknown schema reference: {reference}")
        if name in seen:
            raise SchemaReferenceError(f"Cyclic schema reference: {reference}")
        return transform(deepcopy(definitions[name]), seen | {name})

    def transform(value: Any, seen: frozenset[str] = frozenset()) -> Any:
        if isinstance(value, list):
            return [transform(item, seen) for item in value]
        if not isinstance(value, dict):
            return value

        if "$ref" in value:
            # Pydantic may attach annotations such as ``default`` or ``title``
            # beside a reference.  Those keywords are stripped everywhere
            # else, so discard them before deciding whether the reference has
            # meaningful siblings that cannot safely be inlined.
            siblings = {
                key: item
                for key, item in value.items()
                if key != "$ref" and key not in _REMOVED_KEYWORDS
            }
            if siblings:
                raise SchemaReferenceError("Referenced schemas cannot have sibling keywords")
            return resolve_reference(value["$ref"], seen)

        transformed = {
            key: transform(item, seen)
            for key, item in value.items()
            if key not in _REMOVED_KEYWORDS
        }
        if transformed.get("type") == "object":
            properties = transformed.setdefault("properties", {})
            transformed["additionalProperties"] = False
            transformed["required"] = list(properties)
        return transformed

    return transform(schema)
