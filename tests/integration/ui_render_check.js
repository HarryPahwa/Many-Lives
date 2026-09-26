// Headless check of the minimap and character panel render logic (TDD §18).
//
// There is no browser in CI, so app.js is evaluated against a minimal DOM stub
// and the resulting element tree is asserted directly. This verifies the two
// things the minimap can get wrong and tests of the API cannot catch:
// walls derived from exits, and the north = +y row order.
//
// Run by tests/integration/test_ui_render.py; exits non-zero on failure.

"use strict";

const fs = require("fs");
const path = require("path");
const assert = require("assert");

// --- minimal DOM ------------------------------------------------------------

class ClassList {
  constructor() {
    this._set = new Set();
  }
  add(...names) {
    for (const n of names) this._set.add(n);
  }
  contains(name) {
    return this._set.has(name);
  }
  get value() {
    return [...this._set].join(" ");
  }
}

class El {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.classList = new ClassList();
    this.style = { setProperty() {}, };
    this._text = "";
    this.title = "";
    this.hidden = false;
    this.disabled = false;
    this.value = "";
  }
  set className(v) {
    for (const n of String(v).split(/\s+/).filter(Boolean)) this.classList.add(n);
  }
  get className() {
    return this.classList.value;
  }
  set textContent(v) {
    this._text = String(v);
  }
  get textContent() {
    return this._text;
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  append(...kids) {
    for (const k of kids) this.children.push(k);
  }
  replaceChildren(...kids) {
    this.children = kids;
  }
  addEventListener() {}
  setAttribute() {}
  focus() {}
}

const registry = new Map();
const document = {
  createElement: (tag) => new El(tag),
  getElementById: (id) => {
    if (!registry.has(id)) registry.set(id, new El("div"));
    return registry.get(id);
  },
  addEventListener: () => {},
};
const windowStub = {
  addEventListener: () => {},
  dispatchEvent: () => {},
  setTimeout: () => 0,
  clearTimeout: () => {},
};

// --- load app.js ------------------------------------------------------------

const source = fs.readFileSync(
  path.join(__dirname, "..", "..", "app", "ui", "static", "app.js"),
  "utf8"
);
const load = new Function(
  "document",
  "window",
  "crypto",
  "fetch",
  "CustomEvent",
  `${source}\nreturn { renderMinimap, renderCharacter, renderRoom };`
);
const ui = load(
  document,
  windowStub,
  { randomUUID: () => "00000000-0000-4000-8000-000000000000" },
  async () => {
    throw new Error("no network in this check");
  },
  class {}
);

// --- fixtures ---------------------------------------------------------------

// A corridor: cell_0_0 opens north onto cell_0_1, which opens north and east.
const map = {
  width: 3,
  height: 3,
  player_cell: "cell_0_1",
  cells: [
    {
      cell_id: "cell_0_0",
      x: 0,
      y: 0,
      state: "DISCOVERED",
      exits: ["north"],
      name: "Boundary Stair",
      boss: false,
    },
    {
      cell_id: "cell_0_1",
      x: 0,
      y: 1,
      state: "DISCOVERED",
      exits: ["south", "east"],
      name: "Dripping Cistern",
      boss: false,
    },
    {
      cell_id: "cell_2_2",
      x: 2,
      y: 2,
      state: "DISCOVERED",
      exits: ["west"],
      name: "Vault",
      boss: true,
    },
    { cell_id: "cell_1_0", x: 1, y: 0, state: "RUMORED", exits: [], name: null, boss: false },
  ],
  rumored: ["cell_1_0"],
};

ui.renderMinimap(map);
const tiles = document.getElementById("minimap").children;

assert.strictEqual(tiles.length, 9, "a 3x3 map must render 9 tiles");

// Row order: north is +y, so the FIRST row rendered is the highest y.
// Index = row * width + x. cell_0_2 (unknown) is index 0; cell_0_0 is index 6.
const at = (x, y) => tiles[(map.height - 1 - y) * map.width + x];

assert.ok(at(0, 2).classList.contains("tile-unknown"), "cell_0_2 is unexplored");
assert.ok(at(0, 0).classList.contains("tile-known"), "cell_0_0 is explored");
assert.ok(at(0, 1).classList.contains("tile-here"), "player marker on cell_0_1");
assert.strictEqual(at(0, 1).textContent, "@", "player tile shows @");

// Walls: cell_0_0 exits north only, so the other three sides are walls.
const spawn = at(0, 0);
assert.ok(!spawn.classList.contains("wall-top"), "north exit must not be a wall");
assert.ok(spawn.classList.contains("wall-bottom"), "no south exit -> wall");
assert.ok(spawn.classList.contains("wall-left"), "no west exit -> wall");
assert.ok(spawn.classList.contains("wall-right"), "no east exit -> wall");

// cell_0_1 exits south and east.
const here = at(0, 1);
assert.ok(!here.classList.contains("wall-bottom"), "south exit must not be a wall");
assert.ok(!here.classList.contains("wall-right"), "east exit must not be a wall");
assert.ok(here.classList.contains("wall-top"), "no north exit -> wall");

// Rumoured cells are hatched and carry no walls (they expose no exits).
const rumoured = at(1, 0);
assert.ok(rumoured.classList.contains("tile-rumored"), "rumoured cell hatched");
for (const side of ["wall-top", "wall-bottom", "wall-left", "wall-right"]) {
  assert.ok(!rumoured.classList.contains(side), `rumoured cell must not draw ${side}`);
}

// Boss marker.
assert.ok(at(2, 2).classList.contains("tile-boss"), "boss cell marked");
assert.strictEqual(at(2, 2).textContent, "B", "boss tile shows B");

// --- character panel --------------------------------------------------------

ui.renderCharacter({
  player_id: "player_1",
  name: "Ada",
  hp: 12,
  max_hp: 20,
  mp: 6,
  max_mp: 6,
  level: 2,
  xp: 44,
  pending_level_ups: 1,
  cell_id: "cell_0_1",
  status: "ALIVE",
  stats: { attack: 5, defense: 2, speed: 4, dodge_pct: 10, skill: 3 },
  carried: [{ id: "item_1", name: "brass key", quantity: 1, slot: null, stackable: false }],
  weapon: null,
  armor: null,
  carried_slots: 6,
  keys_held: 1,
  keys_required: 3,
});

const charText = JSON.stringify(document.getElementById("character"), (k, v) =>
  k === "classList" ? undefined : v
);
assert.ok(charText.includes("Ada"), "character name rendered");
assert.ok(charText.includes("brass key"), "inventory item rendered");
assert.ok(charText.includes("empty"), "empty carried slots rendered");
assert.ok(charText.includes("level-up available"), "pending level-up surfaced");

// --- untrusted text stays text ---------------------------------------------

ui.renderRoom({
  cell_id: "cell_0_1",
  name: "<script>alert(1)</script>",
  description: "<img src=x onerror=alert(1)>",
  exits: ["north"],
  features: [],
  characters: [],
  items: [],
});
const room = document.getElementById("room");
assert.strictEqual(
  room.children[0].textContent,
  "<script>alert(1)</script>",
  "markup must land as literal text, not as parsed HTML"
);

console.log("ui_render_check: all assertions passed");
