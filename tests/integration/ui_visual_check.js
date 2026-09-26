// Headless DOM check for the room-visuals panel (Room Visuals §14.3).
//
// Deliberately a separate file from ui_render_check.js: that script is an
// existing regression gate and must keep passing unchanged, and adding a name
// to its `return {...}` list would modify it.
//
// What this proves that an API test cannot: the panel writes every string with
// textContent, sets img.src only from the server-supplied URL, and hides
// itself when the feature is off.

"use strict";

const fs = require("fs");
const path = require("path");
const assert = require("assert");

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
    this.style = { setProperty() {} };
    this._text = "";
    this._attrs = {};
    this.title = "";
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.alt = "";
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
  setAttribute(k, v) {
    this._attrs[k] = String(v);
  }
  getAttribute(k) {
    return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null;
  }
  removeAttribute(k) {
    delete this._attrs[k];
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
  localStorage: {
    getItem() {
      return null;
    },
    setItem() {},
  },
};

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
  `${source}\nreturn { renderVisual, hideVisualPanel, state };`
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

const panel = document.getElementById("visual-panel");
const img = document.getElementById("visual-img");
const badge = document.getElementById("visual-badge");
const caption = document.getElementById("visual-caption");
const button = document.getElementById("visual-btn");

// --- a ready image ----------------------------------------------------------

ui.renderVisual({
  cell_id: "cell_1_2",
  status: "READY",
  dirty: false,
  revision: 3,
  image_url: "/api/campaigns/cmp_1/visual-assets/va_" + "a".repeat(32),
  auto_update: false,
  error_code: null,
});

assert.strictEqual(panel.hidden, false, "a READY visual shows the panel");
assert.strictEqual(
  img.getAttribute("src"),
  "/api/campaigns/cmp_1/visual-assets/va_" + "a".repeat(32),
  "src comes from the server-supplied URL"
);
assert.strictEqual(badge.textContent, "", "a current image needs no badge");
assert.ok(caption.textContent.includes("3"), "caption shows the revision");
assert.strictEqual(button.hidden, true, "nothing to do when READY and clean");

// --- out of date ------------------------------------------------------------

ui.renderVisual({
  cell_id: "cell_1_2",
  status: "READY",
  dirty: true,
  revision: 3,
  image_url: "/api/campaigns/cmp_1/visual-assets/va_" + "b".repeat(32),
  auto_update: false,
  error_code: null,
});
assert.strictEqual(badge.textContent, "Out of date");
assert.strictEqual(button.hidden, false);
assert.strictEqual(button.textContent, "Update visual");

// --- generating -------------------------------------------------------------

ui.renderVisual({
  cell_id: "cell_1_2",
  status: "GENERATING",
  dirty: false,
  revision: 3,
  image_url: "/api/campaigns/cmp_1/visual-assets/va_" + "b".repeat(32),
  auto_update: false,
  error_code: null,
});
assert.strictEqual(badge.textContent, "Generating…");
assert.strictEqual(button.disabled, true, "no second request while one runs");
assert.ok(
  img.getAttribute("src"),
  "the previous picture stays on screen while generating"
);

// --- failed -----------------------------------------------------------------

ui.renderVisual({
  cell_id: "cell_1_2",
  status: "FAILED",
  dirty: false,
  revision: 3,
  image_url: "/api/campaigns/cmp_1/visual-assets/va_" + "b".repeat(32),
  auto_update: false,
  error_code: "HTTP_5XX",
});
assert.strictEqual(badge.textContent, "Failed — retry");
assert.strictEqual(button.disabled, false, "a failure must be retryable");
assert.ok(
  !JSON.stringify(badge.textContent).includes("sk-or-"),
  "no key material in the UI"
);

// --- nothing yet ------------------------------------------------------------

ui.renderVisual({
  cell_id: "cell_1_2",
  status: "NONE",
  dirty: false,
  revision: 0,
  image_url: null,
  auto_update: false,
  error_code: null,
});
assert.strictEqual(img.getAttribute("src"), null, "no src when there is no image");
assert.strictEqual(button.textContent, "Generate visual");
assert.strictEqual(caption.textContent, "", "no revision to caption yet");

// --- feature off ------------------------------------------------------------

ui.hideVisualPanel();
assert.strictEqual(panel.hidden, true, "the panel hides when the feature is off");

ui.renderVisual(null);
assert.strictEqual(panel.hidden, true, "a null status hides the panel");

// --- untrusted text stays text ---------------------------------------------

ui.renderVisual({
  cell_id: "<script>window.__pwned=true</script>",
  status: "FAILED",
  dirty: false,
  revision: 0,
  image_url: null,
  auto_update: false,
  error_code: "<img src=x onerror=alert(1)>",
});
// The payload appearing verbatim in a text property is the CORRECT outcome:
// it proves the string was assigned, not parsed. What must never happen is
// the script running, or an element being created from it.
assert.ok(img.alt.includes("<script>"), "the payload lands as literal text");
assert.strictEqual(
  windowStub.__pwned,
  undefined,
  "no markup from a status may ever execute"
);
assert.strictEqual(img.children.length, 0, "no elements built from the payload");
assert.strictEqual(badge.children.length, 0, "badge holds text, not nodes");

// --- the source itself ------------------------------------------------------

for (const sink of ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"]) {
  assert.ok(!source.includes(sink), `app.js must not use ${sink}`);
}

console.log("ui_visual_check: all assertions passed");
