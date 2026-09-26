"""Context builder (TDD §11).

Assembles bounded context per active policy: system contract -> exact state ->
recent exact events -> semantic memory -> NPC knowledge. Returns
(context_text, manifest). Never sends the whole event log. Enforces token
budget with the specified drop order.
Scaffold only.
"""
