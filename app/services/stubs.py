"""In-memory stand-ins for the A and B integration seams (TDD §27.1).

This module is the *executable contract* for Developer C's integration points.
Every function marked ``# STUB(A)`` or ``# STUB(B)`` has the signature the real
implementation must satisfy; the turn orchestrator talks only to the protocols
below, never to a concrete implementation.

**How A and B swap in real code:** implement ``EnginePort`` / ``HarnessPort``
and return it from ``get_engine()`` / ``get_harness()`` at the bottom of this
file. That selector is the only place that chooses, so nothing in
``turn_orchestrator.py`` or ``api/`` has to change.

The stub "models" produce no dice and no state (TDD §5.1, §5.8): the stub
adjudicator returns a typed proposal, the stub narrator returns prose only.
Authority stays with the stub engine, exactly as it will with the real one.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from app.api.schemas import (
    CampaignSummary,
    ContextManifest,
    InventoryItem,
    MapCell,
    MapResponse,
    MemoryRef,
    ModelCall,
    PlayerSheet,
    PlayerState,
    Roll,
    Stats,
    VisibleCell,
    VisibleCharacter,
    VisibleFeature,
    VisibleItem,
)

logger = logging.getLogger("many_lives.stubs")

GRID_W = 7
GRID_H = 7
KEYS_REQUIRED = 3

# NORTH is +y. This matches app/domain/rules.py's DIRECTION_OFFSETS so the
# minimap is not mirrored when A's engine replaces the stub.
DIRECTIONS: dict[str, tuple[int, int]] = {
    "north": (0, 1),
    "south": (0, -1),
    "east": (1, 0),
    "west": (-1, 0),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cell_key(x: int, y: int) -> str:
    return f"cell_{x}_{y}"


def parse_cell_key(key: str) -> tuple[int, int]:
    _, sx, sy = key.split("_")
    return int(sx), int(sy)


class ConcurrencyConflict(RuntimeError):
    """Raised when the campaign moved under us (§9.9) -> HTTP 409."""


# ---------------------------------------------------------------------------
# Seam data contracts (shared by stub and real implementations)
# ---------------------------------------------------------------------------


@dataclass
class Intent:
    """Fast-path parser output. Mirrors app.domain.types.ActionIntent."""

    action_type: str
    actor_id: str
    targets: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Proposal:
    """Adjudicator output (§10.3). Models propose; they never decide."""

    action_type: str
    actor_id: str
    targets: list[str] = field(default_factory=list)
    feasibility: str = "PLAUSIBLE"
    reason: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    proposed_effects_on_success: list[dict[str, Any]] = field(default_factory=list)
    proposed_effects_on_failure: list[dict[str, Any]] = field(default_factory=list)
    utterance: str | None = None


@dataclass
class EngineResolution:
    """Rules-engine output (§8.2 `Resolution`)."""

    accepted: bool
    reason: str | None = None
    effects: list[dict[str, Any]] = field(default_factory=list)
    event_types: list[str] = field(default_factory=list)
    outcome_summary: str = ""
    rolls: list[Roll] = field(default_factory=list)
    rejected_effects: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CommitResult:
    """Repository output of `commit_turn` (§9.9)."""

    turn_sequence: int
    event_ids: list[str] = field(default_factory=list)


@dataclass
class NarrationResult:
    """Narrator output (§10.6): prose plus machine-checkable claims."""

    prose: str
    claims: list[dict[str, Any]] = field(default_factory=list)
    source: str = "MODEL"
    model_call: ModelCall | None = None


@dataclass
class WorldView:
    """The read-only per-turn snapshot the engine and harness both read."""

    campaign_id: str
    player_id: str
    campaign_status: str
    current_turn: int
    player: PlayerState
    visible_cell: VisibleCell
    exits: list[str] = field(default_factory=list)


@dataclass
class TurnRecord:
    """One `turns` document (§9.7): idempotency + metrics + inspector source."""

    campaign_id: str
    turn_id: str
    kind: str
    player_id: str
    status: str
    path: str = "FAST"
    action_class: str | None = None
    #: Machine-readable rejection code (§7.1.3), e.g. ADJUDICATION_FAILED.
    #: The player-facing wording lives in the narration, not here.
    reason_code: str | None = None
    input: str | None = None
    turn_sequence: int = 0
    proposal: dict[str, Any] | None = None
    accepted_effect_types: list[str] = field(default_factory=list)
    rejected_effects: list[dict[str, Any]] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    narration: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    invariants: dict[str, Any] | None = None
    context_manifest: dict[str, Any] | None = None
    model_calls: list[dict[str, Any]] = field(default_factory=list)
    vector_search_ms: int | None = None
    created_at: str = field(default_factory=_now)
    committed_at: str | None = None
    narrated_at: str | None = None


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------


class EnginePort(Protocol):
    """Developer A's seam (§27.1)."""

    #: True when state outlives the process (a real database behind the seam).
    #: The §20.2 restart tests assert persistence only against a durable
    #: engine; they skip, loudly, against the in-memory stub.
    DURABLE: bool

    def create_campaign(self, player_name: str, seed: int | None) -> CampaignSummary: ...

    def list_campaigns(self) -> list[CampaignSummary]: ...

    def get_campaign(self, campaign_id: str) -> CampaignSummary | None: ...

    def load_world_view(self, campaign_id: str, player_id: str) -> WorldView: ...

    def parse_fast_path(self, text: str, actor_id: str) -> Intent | None: ...

    def resolve(self, view: WorldView, intent: Intent) -> EngineResolution: ...

    def commit_turn(
        self, view: WorldView, resolution: EngineResolution, turn_id: str
    ) -> CommitResult: ...

    def generate_room(self, campaign_id: str, key: str) -> None: ...

    def build_map(self, campaign_id: str, player_id: str) -> MapResponse: ...

    def player_sheet(self, campaign_id: str, player_id: str) -> PlayerSheet: ...

    # `turns` collection (§9.7) — idempotency record and inspector source.
    def get_turn(self, campaign_id: str, turn_id: str) -> TurnRecord | None: ...

    def put_turn(self, record: TurnRecord) -> None: ...

    def latest_turn(self, campaign_id: str) -> TurnRecord | None: ...


class HistoryStore(Protocol):
    """Optional engine capability used by the §16.4 stress measurement.

    Deliberately **not** part of ``EnginePort``: the turn loop never needs it,
    so Developer A is not obliged to implement it for the game to run. The
    stress driver checks for it at runtime and says so plainly if it is
    missing.
    """

    def append_events(self, campaign_id: str, events: list[dict[str, Any]]) -> int: ...

    def append_memories(
        self, campaign_id: str, memories: list[dict[str, Any]]
    ) -> int: ...

    def raw_events(self, campaign_id: str) -> list[dict[str, Any]]:
        """The stored event log, for measurement read models."""
        return self._require(campaign_id).events

    def raw_memories(self, campaign_id: str) -> list[dict[str, Any]]:
        return self._require(campaign_id).memories

    def history_stats(self, campaign_id: str) -> dict[str, int]: ...


def supports_history(engine: object) -> bool:
    """Whether the installed engine can store bulk synthetic history."""
    return all(
        callable(getattr(engine, name, None))
        for name in ("append_events", "append_memories", "history_stats")
    )


class HarnessPort(Protocol):
    """Developer B's seam (§27.1)."""

    def classify(self, text: str, view: WorldView) -> str: ...

    def build_context(
        self, view: WorldView, action_class: str, action_text: str | None
    ) -> tuple[str, ContextManifest, int | None]: ...

    def adjudicate(
        self, text: str, context_text: str, view: WorldView, action_class: str
    ) -> tuple[Proposal | None, list[ModelCall]]: ...

    def narrate(
        self, view: WorldView, resolution: EngineResolution, kind: str
    ) -> NarrationResult: ...

    def template_narration(
        self, view: WorldView, resolution: EngineResolution, kind: str
    ) -> str: ...


# ---------------------------------------------------------------------------
# STUB(A) — in-memory engine
# ---------------------------------------------------------------------------


@dataclass
class _Character:
    id: str
    name: str
    status: str = "ALIVE"
    disposition: str | None = None
    hp: int = 8
    max_hp: int = 8


@dataclass
class _Item:
    id: str
    name: str
    where: str = "floor"  # floor | inventory | equipped
    quantity: int = 1
    slot: str | None = None
    is_key: bool = False


@dataclass
class _Cell:
    key: str
    x: int
    y: int
    name: str = ""
    description: str = ""
    generated: bool = False
    boss: bool = False
    features: list[VisibleFeature] = field(default_factory=list)
    characters: list[_Character] = field(default_factory=list)
    items: list[_Item] = field(default_factory=list)


@dataclass
class _Campaign:
    campaign_id: str
    player_name: str
    seed: int
    status: str = "ACTIVE"
    current_turn: int = 0
    player_id: str = "player_1"
    player_cell: str = cell_key(0, 0)
    hp: int = 20
    max_hp: int = 20
    mp: int = 6
    max_mp: int = 6
    level: int = 1
    xp: int = 0
    adjacency: dict[str, list[str]] = field(default_factory=dict)
    cells: dict[str, _Cell] = field(default_factory=dict)
    discovered: set[str] = field(default_factory=set)
    rumored: set[str] = field(default_factory=set)
    boss_cell: str = cell_key(6, 6)
    inventory: list[_Item] = field(default_factory=list)
    updated_at: str = field(default_factory=_now)
    turns: dict[str, TurnRecord] = field(default_factory=dict)
    turn_order: list[str] = field(default_factory=list)
    # Append-only consequential history (§9.5) and derived memories (§9.6).
    # The orchestrator never reads these; they exist so stored history is a
    # real, countable thing for the §16.4 bounded-context measurement.
    events: list[dict[str, Any]] = field(default_factory=list)
    memories: list[dict[str, Any]] = field(default_factory=list)


def _build_topology(rng: random.Random) -> dict[str, list[str]]:
    """Randomized spanning tree plus a few restored edges (§14.1, simplified).

    Deterministic for a fixed seed, symmetric, and connected — the three
    properties §20.1 tests and the minimap depend on.
    """
    parent: dict[str, str] = {}

    def find(a: str) -> str:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    edges: list[tuple[str, str]] = []
    for x in range(GRID_W):
        for y in range(GRID_H):
            parent[cell_key(x, y)] = cell_key(x, y)
            if x + 1 < GRID_W:
                edges.append((cell_key(x, y), cell_key(x + 1, y)))
            if y + 1 < GRID_H:
                edges.append((cell_key(x, y), cell_key(x, y + 1)))

    rng.shuffle(edges)
    adjacency: dict[str, list[str]] = {
        cell_key(x, y): [] for x in range(GRID_W) for y in range(GRID_H)
    }
    leftover: list[tuple[str, str]] = []
    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra == rb:
            leftover.append((a, b))
            continue
        parent[ra] = rb
        adjacency[a].append(b)
        adjacency[b].append(a)

    # Restore ~15% of the rejected edges so the map has loops, not a pure tree.
    for a, b in leftover:
        if rng.random() < 0.15:
            adjacency[a].append(b)
            adjacency[b].append(a)

    return {k: sorted(v) for k, v in adjacency.items()}


_ROOM_NAMES = [
    "Collapsed Antechamber",
    "Dripping Cistern",
    "Hall of Split Pillars",
    "Rope Bridge Landing",
    "Chapel of Cold Lanterns",
    "Store Room",
    "Sunken Stair",
    "Bone Gallery",
]


class StubEngine:
    """# STUB(A) — everything A owns, in a dict. No Mongo, no transactions.

    Replaced by `services/campaign_service.py` + `persistence/repositories.py`.
    """

    # In-memory: state dies with the process. A's engine sets this True.
    DURABLE = False

    def __init__(self) -> None:
        self._campaigns: dict[str, _Campaign] = {}
        self._counter = 0
        self._lock = threading.Lock()

    # ---- campaign lifecycle (§7.2) ----

    def create_campaign(self, player_name: str, seed: int | None) -> CampaignSummary:
        with self._lock:
            self._counter += 1
            cid = f"cmp_stub{self._counter:08d}"
        actual_seed = seed if seed is not None else random.getrandbits(31)
        rng = random.Random(actual_seed)
        camp = _Campaign(campaign_id=cid, player_name=player_name, seed=actual_seed)
        camp.adjacency = _build_topology(rng)
        camp.cells = {
            cell_key(x, y): _Cell(key=cell_key(x, y), x=x, y=y)
            for x in range(GRID_W)
            for y in range(GRID_H)
        }
        camp.cells[camp.boss_cell].boss = True
        # The spawn cell is generated immediately so the first response can
        # describe it (§7.2 step 4); its archetype is always EMPTY.
        self._dress(camp, camp.player_cell, rng, empty=True)
        camp.discovered.add(camp.player_cell)
        # One rumored cell, so fog-of-war hatching is visible from turn 1.
        camp.rumored.add(cell_key(4, 5))
        self._campaigns[cid] = camp
        return self._summary(camp)

    def list_campaigns(self) -> list[CampaignSummary]:
        return [
            self._summary(c)
            for c in sorted(
                self._campaigns.values(), key=lambda c: c.updated_at, reverse=True
            )
        ]

    def get_campaign(self, campaign_id: str) -> CampaignSummary | None:
        camp = self._campaigns.get(campaign_id)
        return self._summary(camp) if camp else None

    def _summary(self, camp: _Campaign) -> CampaignSummary:
        return CampaignSummary(
            campaign_id=camp.campaign_id,
            status=camp.status,
            current_turn=camp.current_turn,
            player_id=camp.player_id,
            player_name=camp.player_name,
            updated_at=camp.updated_at,
        )

    def _require(self, campaign_id: str) -> _Campaign:
        camp = self._campaigns.get(campaign_id)
        if camp is None:
            raise KeyError(campaign_id)
        return camp

    # ---- room generation (§7.3) ----

    def _dress(
        self, camp: _Campaign, key: str, rng: random.Random, empty: bool = False
    ) -> None:
        """Stand-in for room planner (code) + room dresser (model) + validate."""
        cell = camp.cells[key]
        if cell.generated:
            return  # §5.5 / rule 9: a generated room is never regenerated.
        x, y = cell.x, cell.y
        if empty:
            cell.name = "Boundary Stair"
            cell.description = (
                "Cold air moves up a narrow stair behind you. The way back is gone."
            )
        elif cell.boss:
            cell.name = "Vault of the Pale Warden"
            cell.description = "A banded door stands shut at the far end of the hall."
        else:
            cell.name = _ROOM_NAMES[(x * 7 + y) % len(_ROOM_NAMES)]
            cell.description = "Dust, old stone, and the smell of standing water."
            roll = rng.random()
            if roll < 0.30:
                cell.characters.append(
                    _Character(id=f"npc_{x}{y}a", name="Mara", disposition="WARY")
                )
                cell.features.append(
                    VisibleFeature(
                        id=f"feat_{key}_0",
                        name="wooden chair",
                        state={"posture": "upright"},
                    )
                )
            elif roll < 0.55:
                cell.characters.append(
                    _Character(
                        id=f"enemy_{x}{y}a",
                        name="cave goblin",
                        disposition="HOSTILE",
                        hp=6,
                        max_hp=6,
                    )
                )
            elif roll < 0.75:
                cell.items.append(
                    _Item(id=f"item_{x}{y}a", name="brass key", is_key=True)
                )
            else:
                cell.features.append(
                    VisibleFeature(
                        id=f"feat_{key}_0", name="iron brazier", state={"lit": "unlit"}
                    )
                )
        cell.generated = True

    def generate_room(self, campaign_id: str, key: str) -> None:
        camp = self._require(campaign_id)
        x, y = parse_cell_key(key)
        self._dress(camp, key, random.Random(camp.seed + x * 31 + y))

    # ---- reads (§27.1 `load_world_view`) ----

    def load_world_view(self, campaign_id: str, player_id: str) -> WorldView:
        camp = self._require(campaign_id)
        return WorldView(
            campaign_id=camp.campaign_id,
            player_id=player_id,
            campaign_status=camp.status,
            current_turn=camp.current_turn,
            player=self._player_state(camp),
            visible_cell=self._visible_cell(camp, camp.player_cell),
            exits=self._exits(camp, camp.player_cell),
        )

    def _player_state(self, camp: _Campaign) -> PlayerState:
        return PlayerState(
            hp=camp.hp,
            max_hp=camp.max_hp,
            mp=camp.mp,
            max_mp=camp.max_mp,
            level=camp.level,
            xp=camp.xp,
            pending_level_ups=0,
            cell_id=camp.player_cell,
        )

    def _exits(self, camp: _Campaign, key: str) -> list[str]:
        x, y = parse_cell_key(key)
        out = []
        for name, (dx, dy) in DIRECTIONS.items():
            if cell_key(x + dx, y + dy) in camp.adjacency.get(key, []):
                out.append(name)
        return out

    def _visible_cell(self, camp: _Campaign, key: str) -> VisibleCell:
        cell = camp.cells[key]
        return VisibleCell(
            cell_id=key,
            name=cell.name or "Unlit Passage",
            description=cell.description,
            exits=self._exits(camp, key),
            features=list(cell.features),
            characters=[
                VisibleCharacter(
                    id=c.id, name=c.name, status=c.status, disposition=c.disposition
                )
                for c in cell.characters
            ],
            items=[VisibleItem(id=i.id, name=i.name, where=i.where) for i in cell.items],
        )

    def build_map(self, campaign_id: str, player_id: str) -> MapResponse:
        camp = self._require(campaign_id)
        cells: list[MapCell] = []
        for key in sorted(camp.discovered):
            x, y = parse_cell_key(key)
            cell = camp.cells[key]
            cells.append(
                MapCell(
                    cell_id=key,
                    x=x,
                    y=y,
                    state="DISCOVERED",
                    exits=self._exits(camp, key),
                    name=cell.name or None,
                    boss=cell.boss,
                )
            )
        for key in sorted(camp.rumored - camp.discovered):
            x, y = parse_cell_key(key)
            # §17.3: exits are returned only for discovered cells.
            cells.append(MapCell(cell_id=key, x=x, y=y, state="RUMORED", boss=False))
        return MapResponse(
            width=GRID_W,
            height=GRID_H,
            player_cell=camp.player_cell,
            cells=cells,
            rumored=sorted(camp.rumored - camp.discovered),
        )

    def player_sheet(self, campaign_id: str, player_id: str) -> PlayerSheet:
        camp = self._require(campaign_id)
        carried = [
            InventoryItem(
                id=i.id, name=i.name, quantity=i.quantity, stackable=not i.is_key
            )
            for i in camp.inventory
            if i.where == "inventory"
        ]
        return PlayerSheet(
            player_id=camp.player_id,
            name=camp.player_name,
            hp=camp.hp,
            max_hp=camp.max_hp,
            mp=camp.mp,
            max_mp=camp.max_mp,
            level=camp.level,
            xp=camp.xp,
            cell_id=camp.player_cell,
            status="ALIVE" if camp.hp > 0 else "DEAD",
            stats=Stats(attack=5, defense=2, speed=4, dodge_pct=10, skill=3),
            carried=carried,
            weapon=None,
            armor=None,
            keys_held=sum(
                1 for i in camp.inventory if i.is_key and i.where == "inventory"
            ),
            keys_required=KEYS_REQUIRED,
        )

    # ---- fast path (§13.2) ----

    def parse_fast_path(self, text: str, actor_id: str) -> Intent | None:
        clean = text.strip().lower()
        if not clean:
            return None
        short = {"n": "north", "s": "south", "e": "east", "w": "west"}
        clean = short.get(clean, clean)
        if clean in DIRECTIONS:
            return Intent("MOVE", actor_id, params={"direction": clean})
        parts = clean.split(maxsplit=1)
        cmd, arg = parts[0], (parts[1] if len(parts) > 1 else "")
        arg = short.get(arg, arg)
        if cmd in ("go", "move") and arg in DIRECTIONS:
            return Intent("MOVE", actor_id, params={"direction": arg})
        if cmd == "look":
            return Intent("LOOK", actor_id)
        if cmd == "wait":
            return Intent("WAIT", actor_id)
        if cmd == "take" and arg:
            return Intent("TAKE_ITEM", actor_id, targets=[arg])
        if cmd == "attack" and arg:
            return Intent("ATTACK", actor_id, targets=[arg])
        return None

    # ---- resolve (§7.1 step 4) ----

    def resolve(self, view: WorldView, intent: Intent) -> EngineResolution:
        camp = self._require(view.campaign_id)
        at = intent.action_type

        if at == "MOVE":
            direction = intent.params.get("direction")
            if direction not in DIRECTIONS:
                return EngineResolution(False, reason=f"Unknown direction '{direction}'.")
            x, y = parse_cell_key(camp.player_cell)
            dx, dy = DIRECTIONS[direction]
            target = cell_key(x + dx, y + dy)
            if target not in camp.adjacency.get(camp.player_cell, []):
                return EngineResolution(False, reason=f"A wall blocks the way {direction}.")
            return EngineResolution(
                True,
                effects=[
                    {"type": "MOVE_ENTITY", "target_id": camp.player_id, "to": target}
                ],
                event_types=["PLAYER_MOVED", "CELL_DISCOVERED"],
                outcome_summary=f"You go {direction}.",
            )

        if at == "LOOK":
            return EngineResolution(True, outcome_summary="You take in the room.")

        if at == "WAIT":
            return EngineResolution(True, outcome_summary="You wait, listening.")

        if at == "TAKE_ITEM":
            want = (intent.targets or [""])[0].lower()
            cell = camp.cells[camp.player_cell]
            match = next((i for i in cell.items if want in i.name.lower()), None)
            if match is None:
                return EngineResolution(False, reason=f"There is no {want} here.")
            if sum(1 for i in camp.inventory if i.where == "inventory") >= 6:
                return EngineResolution(False, reason="Your hands and pack are full.")
            return EngineResolution(
                True,
                effects=[
                    {"type": "TRANSFER_ITEM", "target_id": match.id, "to": "INVENTORY"}
                ],
                event_types=["ITEM_TRANSFERRED"],
                outcome_summary=f"You take the {match.name}.",
            )

        if at == "ATTACK":
            want = (intent.targets or [""])[0].lower()
            cell = camp.cells[camp.player_cell]
            target = next(
                (
                    c
                    for c in cell.characters
                    if want in c.name.lower() and c.status == "ALIVE"
                ),
                None,
            )
            if target is None:
                return EngineResolution(False, reason=f"There is no {want} to attack.")
            # Code RNG only (§5.8) — never model output.
            rng = random.Random(f"{camp.seed}:{camp.current_turn}:{target.id}")
            roll = rng.randint(1, 20)
            damage = max(1, 5 + rng.choice([-1, 0, 1]) - 1)
            died = target.hp - damage <= 0
            return EngineResolution(
                True,
                effects=[{"type": "DAMAGE", "target_id": target.id, "amount": damage}],
                event_types=["ATTACK_RESOLVED"] + (["ENTITY_DIED"] if died else []),
                outcome_summary=(
                    f"You hit {target.name} for {damage}."
                    + (f" {target.name} falls." if died else "")
                ),
                rolls=[Roll(purpose="attack", sides=20, value=roll)],
            )

        return EngineResolution(False, reason=f"You cannot do that yet ({at}).")

    # ---- commit (§9.9) ----

    def commit_turn(
        self, view: WorldView, resolution: EngineResolution, turn_id: str
    ) -> CommitResult:
        camp = self._require(view.campaign_id)
        # Compare-and-set on current_turn: the durable guard of §9.9 step 1.
        if camp.current_turn != view.current_turn:
            raise ConcurrencyConflict(camp.campaign_id)
        seq = camp.current_turn + 1

        for effect in resolution.effects:
            kind = effect.get("type")
            if kind == "MOVE_ENTITY":
                target = effect["to"]
                camp.player_cell = target
                if not camp.cells[target].generated:
                    self.generate_room(camp.campaign_id, target)
                camp.discovered.add(target)
                camp.rumored.discard(target)
            elif kind == "TRANSFER_ITEM":
                cell = camp.cells[camp.player_cell]
                item = next((i for i in cell.items if i.id == effect["target_id"]), None)
                if item is not None:
                    cell.items.remove(item)
                    item.where = "inventory"
                    camp.inventory.append(item)
            elif kind == "DAMAGE":
                for cell in camp.cells.values():
                    for ch in cell.characters:
                        if ch.id == effect["target_id"]:
                            ch.hp = max(0, ch.hp - int(effect["amount"]))
                            if ch.hp == 0:
                                ch.status = "DEAD"
                                ch.disposition = None

        event_ids = [f"evt_{seq}_{i}" for i in range(len(resolution.event_types))]
        for index, event_type in enumerate(resolution.event_types):
            camp.events.append(
                {
                    "event_id": event_ids[index],
                    "campaign_id": camp.campaign_id,
                    "turn_sequence": seq,
                    "event_index": index,
                    "turn_id": turn_id,
                    "type": event_type,
                    "actor_id": camp.player_id,
                    "cell_id": camp.player_cell,
                    "summary": resolution.outcome_summary,
                    "created_at": _now(),
                }
            )
        camp.current_turn = seq
        camp.updated_at = _now()
        return CommitResult(turn_sequence=seq, event_ids=event_ids)

    # ---- `turns` collection (§9.7) ----

    def get_turn(self, campaign_id: str, turn_id: str) -> TurnRecord | None:
        return self._require(campaign_id).turns.get(turn_id)

    def put_turn(self, record: TurnRecord) -> None:
        camp = self._require(record.campaign_id)
        if record.turn_id not in camp.turns:
            camp.turn_order.append(record.turn_id)
        camp.turns[record.turn_id] = record

    def latest_turn(self, campaign_id: str) -> TurnRecord | None:
        camp = self._require(campaign_id)
        if not camp.turn_order:
            return None
        return camp.turns[camp.turn_order[-1]]

    # ---- history store (§9.5, §9.6) — optional capability ----

    def append_events(self, campaign_id: str, events: list[dict[str, Any]]) -> int:
        """Append a batch of events. Mirrors an insert_many (§16.4)."""
        camp = self._require(campaign_id)
        camp.events.extend(events)
        return len(camp.events)

    def append_memories(
        self, campaign_id: str, memories: list[dict[str, Any]]
    ) -> int:
        camp = self._require(campaign_id)
        camp.memories.extend(memories)
        return len(camp.memories)

    def raw_events(self, campaign_id: str) -> list[dict[str, Any]]:
        """The stored event log, for measurement read models."""
        return self._require(campaign_id).events

    def raw_memories(self, campaign_id: str) -> list[dict[str, Any]]:
        return self._require(campaign_id).memories

    def history_stats(self, campaign_id: str) -> dict[str, int]:
        """Counts and an approximate stored size for the bounded-context table."""
        camp = self._require(campaign_id)
        events_bytes = sum(len(json.dumps(e, default=_encode)) for e in camp.events)
        memory_bytes = sum(len(json.dumps(m, default=_encode)) for m in camp.memories)
        return {
            "events": len(camp.events),
            "memories": len(camp.memories),
            "turns": len(camp.turns),
            "stored_bytes": events_bytes + memory_bytes,
        }


# ---------------------------------------------------------------------------
# STUB(B) — in-memory harness
# ---------------------------------------------------------------------------


class StubHarness:
    """# STUB(B) — canned adjudication and narration. No provider calls.

    Replaced by `harness/adjudicator.py`, `harness/narrator.py`, and
    `harness/context_builder.py`.
    """

    POLICY_VERSION = 1

    def classify(self, text: str, view: WorldView) -> str:
        low = text.lower()
        if any(w in low for w in ("say", "ask", "tell", "persuade", "talk", "mara")):
            return "SOCIAL"
        if any(w in low for w in ("attack", "hit", "stab", "kill", "swing")):
            return "COMBAT"
        if any(w in low for w in ("take", "grab", "open", "chest", "key", "equip")):
            return "ITEM"
        return "EXPLORATION"

    def build_context(
        self, view: WorldView, action_class: str, action_text: str | None
    ) -> tuple[str, ContextManifest, int | None]:
        components = ["player_state", "current_cell", "recent_events"]
        if action_class == "SOCIAL":
            components += ["npc_disposition", "semantic_memory"]
        entity_ids = [view.player_id] + [c.id for c in view.visible_cell.characters]
        manifest = ContextManifest(
            policy_version=self.POLICY_VERSION,
            components=components,
            entity_ids=entity_ids,
            event_ids=[
                f"evt_{max(0, view.current_turn - i)}_0" for i in range(1, 3)
            ],
            memories=(
                [
                    MemoryRef(
                        id="mem_stub0001",
                        score=0.81,
                        text="Mara was struck once before.",
                    )
                ]
                if action_class == "SOCIAL"
                else []
            ),
            estimated_tokens=420 + 90 * len(components),
            notes=["STUB(B): canned context; no retrieval performed."],
        )
        text = f"[STUB CONTEXT class={action_class} cell={view.visible_cell.cell_id}]"
        return text, manifest, None

    def adjudicate(
        self, text: str, context_text: str, view: WorldView, action_class: str
    ) -> tuple[Proposal | None, list[ModelCall]]:
        """Free-text path. The stub proposes only LOOK — never a state change.

        Player text is untrusted (§5.11): an instruction embedded in it is an
        attempted action, not an authority. The stub deliberately proposes
        nothing mutating, so a prompt-injection attempt is inert here as well
        as at the engine.
        """
        call = ModelCall(
            role="ADJUDICATOR",
            model="stub/fake-adjudicator",
            input_tokens=len(text) // 4 + 300,
            output_tokens=40,
            latency_ms=5,
            attempts=1,
            schema_valid=True,
        )
        proposal = Proposal(
            action_type="LOOK",
            actor_id=view.player_id,
            feasibility="PLAUSIBLE",
            reason="STUB(B): free-form input acknowledged without mechanical effect.",
            proposed_effects_on_success=[],
            proposed_effects_on_failure=[],
        )
        return proposal, [call]

    def narrate(
        self, view: WorldView, resolution: EngineResolution, kind: str
    ) -> NarrationResult:
        prose = self.template_narration(view, resolution, kind)
        call = ModelCall(
            role="NARRATOR",
            model="stub/fake-narrator",
            input_tokens=380,
            output_tokens=60,
            latency_ms=7,
            attempts=1,
            schema_valid=True,
        )
        claims = [
            {
                "entity_id": view.player_id,
                "attribute": "cell_id",
                "value": view.player.cell_id,
            },
            {"entity_id": view.player_id, "attribute": "hp", "value": view.player.hp},
        ]
        return NarrationResult(prose=prose, claims=claims, source="MODEL", model_call=call)

    def template_narration(
        self, view: WorldView, resolution: EngineResolution, kind: str
    ) -> str:
        """Deterministic fallback narration built from committed results (§7.1.7)."""
        cell = view.visible_cell
        if kind == "RESUME":
            head = f"You are in the {cell.name}."
        elif not resolution.accepted:
            return resolution.reason or "Nothing comes of it."
        else:
            head = f"{resolution.outcome_summary} You are in the {cell.name}."
        parts = [head]
        if cell.description:
            parts.append(cell.description)
        living = [c for c in cell.characters if c.status == "ALIVE"]
        if living:
            parts.append(
                "Here: "
                + ", ".join(f"{c.name} ({c.disposition or 'NEUTRAL'})" for c in living)
                + "."
            )
        fallen = [c for c in cell.characters if c.status == "DEAD"]
        if fallen:
            parts.append(", ".join(c.name for c in fallen) + " lies dead here.")
        if cell.items:
            parts.append("On the floor: " + ", ".join(i.name for i in cell.items) + ".")
        if cell.exits:
            parts.append("Exits: " + ", ".join(sorted(cell.exits)) + ".")
        else:
            parts.append("There is no way out but the way you came.")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# STUB(A) — file-backed engine: durability without Atlas
# ---------------------------------------------------------------------------


def _encode(value: Any) -> Any:
    """JSON default hook for the few non-JSON types in the world."""
    if isinstance(value, set):
        return sorted(value)
    if hasattr(value, "model_dump"):  # pydantic models (VisibleFeature)
        return value.model_dump()
    raise TypeError(f"cannot serialise {type(value).__name__}")


class FileBackedEngine(StubEngine):
    """# STUB(A) — the in-memory world, written to a JSON file after each write.

    This exists so the §29.2 demo beat — kill the process, resume, state is
    correct — can be shown **before** Atlas is wired up, and so it still works
    if Atlas is unavailable on the day. It is deliberately not a database: no
    transactions, no concurrency beyond the orchestrator's per-campaign lock,
    one file rewritten per turn.

    It is insurance, not a destination. When A's engine lands, `get_engine()`
    returns that instead and this class stops being used.

    Enabled with ``STUB_STATE_FILE=<path>``.
    """

    DURABLE = True

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = Path(path)
        # The orchestrator's locks are per campaign, so two campaigns can be
        # in flight at once and both reach _save(). Without this, they would
        # race on one shared temp path and interleave the whole world file.
        self._save_lock = threading.Lock()
        self._load()

    # ---- persistence ----

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._load_unsafe()
        except Exception as exc:  # noqa: BLE001
            # Anything at all — unreadable file, malformed JSON, or a document
            # written by an older field layout (which raises TypeError from the
            # dataclass constructors below). None of it may take the server down
            # mid-demo: quarantine the file and start empty.
            logger.error("could not load %s (%s); starting empty", self._path, exc)
            self._campaigns.clear()
            self._counter = 0
            try:
                self._path.replace(self._path.with_suffix(".corrupt"))
            except OSError:  # pragma: no cover - best effort
                pass

    def _load_unsafe(self) -> None:
        raw = json.loads(self._path.read_text(encoding="utf-8"))

        self._counter = raw.get("counter", 0)
        for doc in raw.get("campaigns", []):
            camp = _Campaign(
                campaign_id=doc["campaign_id"],
                player_name=doc["player_name"],
                seed=doc["seed"],
                status=doc["status"],
                current_turn=doc["current_turn"],
                player_id=doc["player_id"],
                player_cell=doc["player_cell"],
                hp=doc["hp"],
                max_hp=doc["max_hp"],
                mp=doc["mp"],
                max_mp=doc["max_mp"],
                level=doc["level"],
                xp=doc["xp"],
                adjacency=doc["adjacency"],
                discovered=set(doc["discovered"]),
                rumored=set(doc["rumored"]),
                boss_cell=doc["boss_cell"],
                inventory=[_Item(**i) for i in doc["inventory"]],
                updated_at=doc["updated_at"],
                turn_order=list(doc.get("turn_order", [])),
            )
            camp.cells = {
                key: _Cell(
                    key=c["key"],
                    x=c["x"],
                    y=c["y"],
                    name=c["name"],
                    description=c["description"],
                    generated=c["generated"],
                    boss=c["boss"],
                    features=[VisibleFeature(**f) for f in c["features"]],
                    characters=[_Character(**ch) for ch in c["characters"]],
                    items=[_Item(**i) for i in c["items"]],
                )
                for key, c in doc["cells"].items()
            }
            camp.turns = {
                turn_id: TurnRecord(**record)
                for turn_id, record in doc.get("turns", {}).items()
            }
            self._campaigns[camp.campaign_id] = camp

        logger.info("loaded %d campaign(s) from %s", len(self._campaigns), self._path)

    def _save(self) -> None:
        with self._save_lock:
            self._save_locked()

    def _save_locked(self) -> None:
        payload = {
            "counter": self._counter,
            "campaigns": [
                {
                    **{
                        field: getattr(camp, field)
                        for field in (
                            "campaign_id",
                            "player_name",
                            "seed",
                            "status",
                            "current_turn",
                            "player_id",
                            "player_cell",
                            "hp",
                            "max_hp",
                            "mp",
                            "max_mp",
                            "level",
                            "xp",
                            "adjacency",
                            "discovered",
                            "rumored",
                            "boss_cell",
                            "updated_at",
                            "turn_order",
                        )
                    },
                    "inventory": [asdict(i) for i in camp.inventory],
                    "cells": {k: asdict(c) for k, c in camp.cells.items()},
                    "turns": {k: asdict(r) for k, r in camp.turns.items()},
                }
                for camp in list(self._campaigns.values())
            ],
        }
        # Write to a temporary file and replace, so a crash mid-write cannot
        # leave a half-written world behind. The temp name carries the thread
        # id as well, so a stray concurrent writer can never share it.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(f".{threading.get_ident()}.tmp")
        try:
            tmp.write_text(
                json.dumps(payload, default=_encode, indent=1), encoding="utf-8"
            )
            tmp.replace(self._path)
        finally:
            # replace() consumes the temp file; this only matters if the write
            # itself raised part-way through.
            if tmp.exists():  # pragma: no cover - failure path
                tmp.unlink(missing_ok=True)

    # ---- write-through overrides ----

    def create_campaign(self, player_name: str, seed: int | None) -> CampaignSummary:
        summary = super().create_campaign(player_name, seed)
        self._save()
        return summary

    def commit_turn(
        self, view: WorldView, resolution: EngineResolution, turn_id: str
    ) -> CommitResult:
        result = super().commit_turn(view, resolution, turn_id)
        self._save()
        return result

    def put_turn(self, record: TurnRecord) -> None:
        super().put_turn(record)
        self._save()

    def generate_room(self, campaign_id: str, key: str) -> None:
        super().generate_room(campaign_id, key)
        self._save()


# ---------------------------------------------------------------------------
# Selector — THE ONE PLACE stubs are swapped for real implementations
# ---------------------------------------------------------------------------


def _build_engine() -> EnginePort:
    """Choose an engine. A: return your real one from here.

    Order of preference once A's engine exists:
      1. A's Atlas-backed engine when MONGODB_URI is configured;
      2. FileBackedEngine when STUB_STATE_FILE is set (durable, no Atlas);
      3. the in-memory StubEngine.
    """
    state_file = os.getenv("STUB_STATE_FILE", "").strip()
    if state_file:
        return FileBackedEngine(state_file)
    return StubEngine()


_ENGINE: EnginePort = _build_engine()


def _build_harness() -> HarnessPort:
    """Choose B's adapter only when a real model configuration is active."""
    from app.config import get_settings

    if get_settings().use_fake_models:
        return StubHarness()

    from app.harness.model_client import get_model_client
    from app.services.harness_adapter import ProductionHarness

    settings = get_settings()
    if settings.mongodb_uri:
        from app.persistence.mongo import get_database

        return ProductionHarness(client=get_model_client(), db=get_database())
    return ProductionHarness(client=get_model_client())


_HARNESS: HarnessPort = _build_harness()


def get_engine() -> EnginePort:
    """Return the active engine (Developer A's seam).

    A: when `services/campaign_service.py` + `persistence/repositories.py` are
    ready, construct the real adapter in `_build_engine()` (gated on
    `settings.mongodb_uri`). Nothing else in `api/` or `turn_orchestrator.py`
    changes.
    """
    return _ENGINE


def get_harness() -> HarnessPort:
    """Return the active harness (Developer B's seam).

    The real adapter is selected when ``USE_FAKE_MODELS=false``. Tests and
    credential-free development retain the deterministic stub.
    """
    return _HARNESS


def set_engine(engine: EnginePort) -> None:
    """Test/integration hook: install an engine implementation."""
    global _ENGINE
    _ENGINE = engine


def set_harness(harness: HarnessPort) -> None:
    """Test/integration hook: install a harness implementation."""
    global _HARNESS
    _HARNESS = harness


def reset_stubs() -> None:
    """Drop all campaign state and rebuild the configured engine.

    Tests call this between cases. It rebuilds through `_build_engine()` so a
    durable configuration is preserved rather than silently downgraded to the
    in-memory stub — and it deletes the state file, because "drop all state"
    that left a file behind would leak one test's world into the next.
    """
    state_file = os.getenv("STUB_STATE_FILE", "").strip()
    if state_file:
        for path in (Path(state_file), Path(state_file).with_suffix(".tmp")):
            try:
                path.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - best effort
                pass
    set_engine(_build_engine())
    set_harness(_build_harness())
