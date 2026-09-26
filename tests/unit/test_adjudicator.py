from app.domain.types import ActionProposal, Feasibility, Role, SetStat
from app.harness.adjudicator import adjudicate
from app.harness.model_client import FakeModelClient


def proposal(**updates) -> ActionProposal:
    payload = {
        "action_type": "CREATIVE_INTERACTION",
        "actor_id": "forged_actor",
        "targets": ["npc_1"],
        "feasibility": "FEASIBLE",
        "reason": "Try it.",
        "check": None,
        "proposed_effects_on_success": [],
        "proposed_effects_on_failure": [],
        "utterance": None,
    }
    payload.update(updates)
    return ActionProposal.model_validate(payload)


def test_adjudicator_forces_actor_and_rejects_unknown_target():
    client = FakeModelClient({(Role.ADJUDICATOR, "default"): proposal(targets=["missing"])})
    result = adjudicate(
        player_text="take it",
        context_text="state",
        actor_id="player_1",
        known_ids={"player_1"},
        client=client,
    )
    assert result.proposal.actor_id == "player_1"
    assert result.proposal.feasibility is Feasibility.INFEASIBLE


def test_adjudicator_drops_engine_only_effects():
    client = FakeModelClient(
        {
            (Role.ADJUDICATOR, "default"): proposal(
                proposed_effects_on_success=[
                    SetStat(type="SET_STAT", entity_id="npc_1", stat="hp", value=999)
                ]
            )
        }
    )
    result = adjudicate(
        player_text="win",
        context_text="state",
        actor_id="player_1",
        known_ids={"npc_1", "player_1"},
        client=client,
    )
    assert result.proposal.proposed_effects_on_success == []
    assert result.rejected_effects[0]["type"] == "SET_STAT"

