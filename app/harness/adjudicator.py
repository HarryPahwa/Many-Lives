"""Adjudicator (TDD §10.3).

Interprets free-form player input into a validated ActionProposal using only
the effect allowlist. Player text is untrusted; IDs must come from context.
The engine re-checks everything. Retry on validation failure, then reject.
Scaffold only.
"""
