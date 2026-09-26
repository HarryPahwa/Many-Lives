from app.domain.death import award_xp, death_xp, kill_xp, resolve_player_death
from app.domain.rng import TurnRng


def player():
    return {"entity_id": "player", "entity_type": "PLAYER", "version": 0,
            "location": {"kind": "CELL", "ref_id": "cell_2_2", "slot": None},
            "character": {"level": 1, "xp": 0, "pending_level_ups": 0,
                          "hp": 0, "max_hp": 20, "mp": 0, "max_mp": 6,
                          "status": "ALIVE"},
            "player": {"spawn_cell_id": "cell_0_0", "new_cells_since_death": 8,
                       "damage_dealt_since_death": 14, "deaths": 0}}


def item(entity_id, *, quantity=1, kind="INVENTORY"):
    return {"_id": entity_id, "entity_id": entity_id, "entity_type": "ITEM", "version": 0,
            "location": {"kind": kind, "ref_id": "player",
                         "slot": "WEAPON" if kind == "EQUIPPED" else None},
            "item": {"quantity": quantity, "max_stack": 3, "stackable": quantity > 1,
                     "guarded_by": [], "status": "ACTIVE", "quest_critical": False}}


def test_death_xp_formula_and_kill_table():
    assert death_xp(player()) == (40, 40, 10, 10)
    assert [kill_xp(4, level) for level in [1, 2, 3, 4, 5, 6, 7]] == [5, 10, 15, 25, 35, 50, 70]
    assert kill_xp(1, 1, boss=True) == 150


def test_award_xp_carries_across_multiple_thresholds():
    award = award_xp({"level": 1, "xp": 90, "pending_level_ups": 0}, 250)
    assert (award.xp_after, award.pending_level_ups_after, award.thresholds_crossed) == (40, 2, 2)


def test_death_drops_one_from_carried_stack_and_respawns():
    outcome = resolve_player_death(player(), [item("potions", quantity=3), item("sword", kind="EQUIPPED")],
                                   death_cell_id="cell_2_2", living_hostile_ids=["enemy"],
                                   rng=TurnRng(4, 3), turn_sequence=3)
    source = next(value for value in outcome.items if value["entity_id"] == "potions")
    dropped = next(value for value in outcome.items if value["entity_id"] == outcome.dropped_item_id)
    assert source["item"]["quantity"] == 2
    assert dropped["item"]["quantity"] == 1
    assert dropped["location"]["ref_id"] == "cell_2_2"
    assert dropped["item"]["guarded_by"] == ["enemy"]
    assert outcome.player["location"]["ref_id"] == "cell_0_0"
    assert outcome.player["character"]["hp"] == 20
    assert outcome.player["character"]["mp"] == 6
    assert outcome.player["player"]["deaths"] == 1


def test_death_uses_equipment_only_when_no_carried_item():
    outcome = resolve_player_death(player(), [item("sword", kind="EQUIPPED")],
                                   death_cell_id="cell_2_2", living_hostile_ids=[],
                                   rng=TurnRng(4, 3), turn_sequence=3)
    assert outcome.dropped_item_id == "sword"
    assert outcome.items[0]["location"]["kind"] == "CELL"
