from app.domain.types import (
    ActionProposal,
    Claim,
    ClaimAttribute,
    NarrationResult as DomainNarrationResult,
    Role,
)
from app.harness.model_client import FakeModelClient
from app.services.harness_adapter import ProductionHarness
from app.services.stubs import EngineResolution, StubEngine


def _view():
    engine = StubEngine()
    campaign = engine.create_campaign("Ada", 7)
    return engine.load_world_view(campaign.campaign_id, campaign.player_id)


def _client() -> FakeModelClient:
    proposal = ActionProposal(
        action_type="LOOK",
        actor_id="ignored",
        targets=[],
        feasibility="FEASIBLE",
        reason="Look around.",
        check=None,
        proposed_effects_on_success=[],
        proposed_effects_on_failure=[],
        utterance=None,
    )
    narration = DomainNarrationResult(
        prose="You take in the room.",
        claims=[Claim(entity_id="player_1", attribute=ClaimAttribute.PRESENT, value=True)],
    )
    return FakeModelClient(
        {(Role.ADJUDICATOR, "default"): proposal, (Role.NARRATOR, "default"): narration}
    )


def test_production_harness_adapts_real_context_adjudication_and_narration():
    view = _view()
    harness = ProductionHarness(client=_client())

    action_class = harness.classify("I study the room", view)
    context, manifest, vector_search_ms = harness.build_context(
        view, action_class, "I study the room"
    )
    proposal, calls = harness.adjudicate("I study the room", context, view, action_class)
    narration = harness.narrate(view, EngineResolution(accepted=True), "ACTION")

    assert action_class == "CREATIVE"
    assert "[untrusted_player_input]" in context
    assert manifest.estimated_tokens > 0
    assert vector_search_ms is None
    assert proposal is not None
    assert proposal.actor_id == view.player_id
    assert calls[0].role == "ADJUDICATOR"
    assert narration.prose == "You take in the room."
    assert narration.model_call is not None


def test_production_harness_marks_unavailable_vector_search_without_a_database():
    view = _view()
    harness = ProductionHarness(client=_client())

    _, manifest, vector_search_ms = harness.build_context(view, "CREATIVE", "I study the room")

    assert "VECTOR_UNAVAILABLE" in manifest.notes
    assert vector_search_ms is None
