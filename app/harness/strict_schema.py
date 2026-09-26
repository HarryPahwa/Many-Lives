"""Strict JSON Schema helper (TDD §10.1).

strict_schema(model): from Pydantic model_json_schema, set
additionalProperties=false everywhere, mark all properties required
(optionals as anyOf [T, null]), inline $defs if needed, drop defaults/titles/
bounds (bounds enforced post-parse). Test against chosen models early.
Scaffold only.
"""
