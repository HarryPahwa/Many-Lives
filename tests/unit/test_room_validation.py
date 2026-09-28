from pathlib import Path

import pytest
import yaml

from app.domain.types import FeatureDressing
from app.world.fallback import build_fallback_dressing
from app.world.room_planner import plan_room
from app.world.room_validation import RoomValidationError, validate_room_dressing
from app.world.topology import generate_topology


CONFIG_DIR = Path(__file__).parents[2] / "config"
BALANCE = {
    **yaml.safe_load((CONFIG_DIR / "world_gen.yaml").read_text()),
    **yaml.safe_load((CONFIG_DIR / "runtime_rules.yaml").read_text()),
}


def planned_room():
    campaign = {"spawn_cell_id": "cell_0_0", "boss_cell_id": "cell_6_6",
                "topology": generate_topology(5).to_dict(), "ungenerated_key_cell_ids": []}
    cell = {"cell_id": "cell_6_6", "danger_tier": 5,
            "reservations": {"key_item_ids": []}}
    return plan_room(seed=5, cell=cell, campaign=campaign, balance=BALANCE)


def test_missing_and_duplicate_slots_are_reported():
    planned = planned_room()
    valid = build_fallback_dressing(planned, seed=5)
    invalid = valid.model_copy(update={"items": []})
    with pytest.raises(RoomValidationError, match="missing item slots"):
        validate_room_dressing(planned, invalid)


def test_duplicate_names_and_modern_terms_are_rejected():
    planned = planned_room()
    valid = build_fallback_dressing(planned, seed=5)
    duplicate = valid.model_copy(update={"room_name": valid.features[0].name})
    with pytest.raises(RoomValidationError, match="unique"):
        validate_room_dressing(planned, duplicate)
    modern = valid.model_copy(update={"room_name": "Laser Vault"})
    with pytest.raises(RoomValidationError, match="modern technology"):
        validate_room_dressing(planned, modern)


def test_invalid_feature_state_prerequisite_is_rejected():
    planned = planned_room()
    valid = build_fallback_dressing(planned, seed=5)
    feature = FeatureDressing(slot_id=None, kind="stone", name="upright stone",
                              properties=[], initial_state={"orientation": "overturned"})
    invalid = valid.model_copy(update={"features": [feature, *valid.features[1:]]})
    with pytest.raises(RoomValidationError, match="must be movable"):
        validate_room_dressing(planned, invalid)
