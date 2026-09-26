"""Cross-layer domain failures with no infrastructure dependencies."""


class ConcurrencyConflict(RuntimeError):
    """Canonical state changed after a resolution snapshot was loaded."""
