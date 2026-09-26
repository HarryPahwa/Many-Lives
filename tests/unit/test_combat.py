from app.domain.combat import equipment_bonuses, hostile_order, resolve_attack
from app.domain.rng import RollRecord, TurnRng


def character(entity_id, *, kind="ENEMY", hp=10, attack=5, defense=2, speed=4, dodge=0):
    return {"entity_id": entity_id, "entity_type": kind,
            "location": {"kind": "CELL", "ref_id": "cell_0_0", "slot": None},
            "character": {"hp": hp, "max_hp": hp, "attack": attack, "defense": defense,
                          "speed": speed, "dodge_pct": dodge, "status": "ALIVE",
                          "disposition": {}}}


def equipment(entity_id, subtype, bonus):
    return {"entity_id": f"{entity_id}-{subtype}",
            "location": {"kind": "EQUIPPED", "ref_id": entity_id, "slot": subtype},
            "item": {"status": "ACTIVE", "attack_bonus": bonus if subtype == "WEAPON" else 0,
                     "armor_bonus": bonus if subtype == "ARMOR" else 0}}


def test_attack_is_deterministic_and_applies_equipment():
    attacker = character("player", kind="PLAYER")
    defender = character("enemy", hp=12, defense=3)
    items = [equipment("player", "WEAPON", 2), equipment("enemy", "ARMOR", 1)]
    first = resolve_attack(attacker, defender, items=items, rng=TurnRng(8, 1))
    second = resolve_attack(attacker, defender, items=items, rng=TurnRng(8, 1))
    assert first == second
    assert first.weapon_bonus == 2 and first.armor_bonus == 1
    assert first.variance in {-1, 0, 1}
    assert first.damage >= 1
    assert first.hp_after == first.hp_before - first.damage


class DodgeRng:
    def __init__(self):
        self._records = []

    @property
    def records(self):
        return tuple(self._records)

    def roll(self, purpose, sides):
        self._records.append(RollRecord(purpose, "roll", 40, sides))
        return 40

    def choice(self, purpose, values):
        raise AssertionError("variance must not be rolled after a dodge")


def test_dodge_caps_at_40_and_skips_variance():
    outcome = resolve_attack(character("a"), character("d", dodge=99), items=[], rng=DodgeRng())
    assert outcome.dodged and outcome.damage == 0
    assert outcome.effective_dodge == 40
    assert outcome.variance is None


def test_hostile_order_and_distraction():
    slow = character("slow", speed=3, attack=9)
    alpha = character("alpha", speed=5, attack=4)
    beta = character("beta", speed=5, attack=4)
    dead = character("dead", speed=10)
    dead["character"]["status"] = "DEAD"
    distracted = character("distracted", speed=9)
    distracted["character"]["distracted_until_turn"] = 2
    assert [entity["entity_id"] for entity in hostile_order(
        [slow, beta, dead, distracted, alpha], "player", turn_sequence=2
    )] == ["alpha", "beta", "slow"]


def test_equipment_ignores_inactive_items():
    weapon = equipment("player", "WEAPON", 3)
    weapon["item"]["status"] = "CONSUMED"
    assert equipment_bonuses("player", [weapon]) == (0, 0)
