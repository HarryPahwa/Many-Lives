from app.domain.mutations import AppendEvent, MutateAttribute, MutationBundle
from app.domain.types import EventType
from scripts.smoke_candidate_generator import (
    has_counterattack,
    has_spoken_opening,
    mock_world,
    summarize,
)


def _bundle(*mutations):
    return MutationBundle(
        bundle_id="candidate",
        action_description="Test outcome",
        rationale="Fixture",
        draft_narration="Something happens.",
        mutations=list(mutations),
    )


def test_mock_world_validates_spoken_dialogue_candidate():
    world, _ = mock_world()
    candidate = _bundle(
        AppendEvent(
            event_type=EventType.DIALOGUE,
            payload={
                "npc_id": "keeper_1",
                "dialogue_intent": "INITIATE_CONVERSATION",
                "npc_reaction": "GUARDED_QUESTION",
            },
            summary="The keeper answers cautiously.",
        )
    )

    report = summarize("talk to the keeper", [candidate], world)

    assert report["valid_count"] == 1
    assert report["dialogue_count"] == 1
    assert report["spoken_opening_count"] == 1
    assert has_spoken_opening(candidate)


def test_counterattack_requires_mechanical_player_damage():
    prose_only = _bundle(
        AppendEvent(
            event_type=EventType.ATTACK_RESOLVED,
            payload={"target_id": "goblin_1", "outcome": "HIT", "damage": 3},
            summary="The goblin counterattacks in the description only.",
        )
    )
    mechanical = _bundle(
        AppendEvent(
            event_type=EventType.ATTACK_RESOLVED,
            payload={"target_id": "player_1", "outcome": "HIT", "damage": 2},
            summary="The goblin hits the player.",
        ),
        MutateAttribute(target_id="player_1", path="stats.hp", value=-2, op="ADD"),
    )

    assert not has_counterattack(prose_only)
    assert has_counterattack(mechanical)
