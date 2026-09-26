from copy import deepcopy

from app.domain.invariants import check_invariants, static_environment_digest


def valid_state():
    topology = {"cell_0_0": ["cell_1_0"], "cell_1_0": ["cell_0_0"]}
    campaign = {"_id": "cmp_test", "current_turn": 0, "spawn_cell_id": "cell_0_0",
                "boss_cell_id": "cell_1_0", "topology": topology,
                "boss_door": {"required_keys": 0, "submitted_key_ids": [], "unlocked": True}}
    environment = {"materials": ["stone"], "lighting": "dim", "smell": "dust",
                   "architectural_notes": "arches"}
    cells = [
        {"campaign_id": "cmp_test", "cell_id": "cell_0_0", "generated": True,
         "room": {"static_environment": environment}, "features": [],
         "static_environment_digest": static_environment_digest(environment)},
        {"campaign_id": "cmp_test", "cell_id": "cell_1_0", "generated": False,
         "room": None, "features": []},
    ]
    player = {"campaign_id": "cmp_test", "entity_id": "player", "entity_type": "PLAYER",
              "location": {"kind": "CELL", "ref_id": "cell_0_0", "slot": None},
              "character": {"hp": 20, "max_hp": 20, "mp": 6, "max_mp": 6,
                            "dodge_pct": 10, "status": "ALIVE"}}
    enemy = {"campaign_id": "cmp_test", "entity_id": "enemy", "entity_type": "ENEMY",
             "origin_cell_id": "cell_0_0",
             "location": {"kind": "CELL", "ref_id": "cell_0_0", "slot": None},
             "character": {"hp": 8, "max_hp": 8, "mp": 0, "max_mp": 0,
                           "dodge_pct": 0, "status": "ALIVE"}}
    return campaign, cells, [player, enemy], [], []


def ids(report):
    return {failure.invariant_id for failure in report.failures}


def test_valid_campaign_checks_all_fifteen_invariants():
    report = check_invariants(*valid_state(), expected_key_count=0)
    assert report.checked == 15 and report.passed


def test_location_owner_capacity_stack_and_resource_failures():
    campaign, cells, entities, events, turns = valid_state()
    entities[0]["character"]["hp"] = 99
    for index in range(7):
        entities.append({"campaign_id": "cmp_test", "entity_id": f"item{index}",
                         "entity_type": "ITEM",
                         "location": {"kind": "INVENTORY", "ref_id": "player", "slot": None},
                         "item": {"subtype": "TRINKET", "quantity": 2, "max_stack": 1,
                                  "stackable": True, "status": "ACTIVE"}})
    entities.append({"campaign_id": "cmp_test", "entity_id": "orphan", "entity_type": "ITEM",
                     "location": {"kind": "INVENTORY", "ref_id": "missing", "slot": None},
                     "item": {"subtype": "TRINKET", "quantity": 1, "max_stack": 1,
                              "stackable": False, "status": "ACTIVE"}})
    assert {"INV-02", "INV-03", "INV-04", "INV-05"}.issubset(
        ids(check_invariants(campaign, cells, entities, events, turns, expected_key_count=0)))


def test_history_baselines_door_topology_events_scope_and_closed_tags():
    campaign, cells, entities, events, turns = valid_state()
    entities[1]["location"]["ref_id"] = "cell_1_0"
    cells[0]["room"]["static_environment"]["smell"] = "flowers"
    cells[0]["features"] = [{"feature_id": "bad", "properties": ["electronic"],
                              "state": {"color": "blue"}}]
    campaign["boss_door"]["unlocked"] = False
    events.extend([
        {"campaign_id": "cmp_test", "event_id": "dead", "turn_sequence": 0,
         "event_index": 0, "type": "ENTITY_DIED", "actor_id": "player",
         "payload": {"entity_id": "enemy"}},
        {"campaign_id": "cmp_test", "event_id": "after", "turn_sequence": 1,
         "event_index": 0, "type": "ATTACK_RESOLVED", "actor_id": "enemy", "payload": {}},
    ])
    report = check_invariants(campaign, cells, entities, events, turns,
                              expected_key_count=0, queries_scoped=False)
    assert {"INV-06", "INV-07", "INV-08", "INV-10", "INV-12", "INV-13", "INV-15"}.issubset(ids(report))


def test_key_and_player_dodge_invariants():
    campaign, cells, entities, events, turns = valid_state()
    entities[0]["character"]["dodge_pct"] = 41
    entities.append({"campaign_id": "cmp_test", "entity_id": "key", "entity_type": "ITEM",
                     "location": {"kind": "NONE", "ref_id": None, "slot": None},
                     "item": {"subtype": "KEY", "quantity": 1, "max_stack": 1,
                              "stackable": True, "status": "DESTROYED"}})
    report = check_invariants(campaign, cells, entities, events, turns, expected_key_count=1)
    assert {"INV-04", "INV-09", "INV-14"}.issubset(ids(report))
