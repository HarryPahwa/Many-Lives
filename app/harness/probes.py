"""Fixed P0.5 probe catalogue and deterministic assessment helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class ProbeAssessment:
    contradictions: int = 0
    invented_entities: int = 0
    absent_entity_mentions: int = 0
    missing_context: bool = False
    retrieval_hit: bool | None = None
    input_tokens: int = 0
    latency_ms: int = 0


@dataclass(frozen=True)
class Probe:
    id: str
    description: str
    # TODO(A): supply a repository-backed canonical fixture for each probe.
    setup: Callable[[], object]
    # TODO(C): invoke this against the harness/engine integration seam once.
    call: Callable[[object], dict[str, object]]
    assess: Callable[[dict[str, object]], ProbeAssessment]


class ProbeFixtureFactory(Protocol):
    """A/C integration seam; B never writes canonical fixture state."""

    def build(self, probe_id: str) -> object: ...


def _integration_required(*_: object) -> object:
    raise NotImplementedError("TODO(A/C): canonical probe fixture and turn runner are not wired")


def _assessment(output: dict[str, object]) -> ProbeAssessment:
    return ProbeAssessment(
        contradictions=int(output.get("contradictions", 0)),
        invented_entities=int(output.get("invented_entities", 0)),
        absent_entity_mentions=int(output.get("absent_entity_mentions", 0)),
        missing_context=bool(output.get("missing_context", False)),
        retrieval_hit=output.get("retrieval_hit") if isinstance(output.get("retrieval_hit"), bool) else None,
        input_tokens=int(output.get("input_tokens", 0)),
        latency_ms=int(output.get("latency_ms", 0)),
    )


_P0 = {
    "P01": "opened chest inventory truth",
    "P02": "stale memory loses to current death state",
    "P03": "long-history feature state",
    "P04": "social disposition context",
    "P05": "stolen key location",
    "P08": "resume fidelity",
    "P12": "wounded enemy state",
    "P14": "campaign-scoped retrieval",
}
PROBES = {
    probe_id: Probe(probe_id, description, _integration_required, _integration_required, _assessment)
    for probe_id, description in _P0.items()
}
