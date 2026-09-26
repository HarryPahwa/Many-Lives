"""Model client (TDD §10.1).

The ONLY module that imports a provider SDK. ModelClient.structured(...) and
.embed(...) over OpenRouter's OpenAI-compatible API with strict JSON Schema,
require_parameters, bounded retry. Includes FakeModelClient for tests/dev.
Scaffold only.
"""
