// Web client (TDD §18).
//
// Two rules this file never breaks:
//   1. Every string that came from a model, the engine, or the player is put
//      on the page with textContent. No HTML-parsing sink is used anywhere in
//      this file, and a test in tests/integration asserts that stays true.
//   2. The client generates turn_id with crypto.randomUUID() and resends the
//      SAME id if a turn has to be retried, so a retry can never apply twice
//      (TDD §7.1, §9.9).

"use strict";

const state = {
  campaignId: null,
  playerId: "player_1",
  inFlight: false,
  // The turn_id for the request currently being retried, if any.
  pendingTurnId: null,
  pendingInput: null,
};

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------

async function api(method, path, body) {
  const options = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    // §17.1 error envelope: {"error": {"code", "message"}}
    const message =
      (payload && payload.error && payload.error.message) ||
      `${response.status} ${response.statusText}`;
    const error = new Error(message);
    error.status = response.status;
    error.code = (payload && payload.error && payload.error.code) || "ERROR";
    throw error;
  }
  return payload;
}

// ---------------------------------------------------------------------------
// Rendering (textContent only)
// ---------------------------------------------------------------------------

function logLine(text, kind) {
  const entry = document.createElement("p");
  entry.className = `log-entry log-${kind}`;
  entry.textContent = text;
  const log = $("narrative");
  log.appendChild(entry);
  log.scrollTop = log.scrollHeight;
  return entry;
}

function clearLog() {
  $("narrative").replaceChildren();
}

function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.hidden = false;
  window.clearTimeout(toast._timer);
  toast._timer = window.setTimeout(() => {
    el.hidden = true;
  }, 4000);
}

function renderMeta(campaign) {
  $("meta-id").textContent = campaign ? campaign.campaign_id : "no campaign";
  $("meta-turn").textContent = campaign ? `turn ${campaign.current_turn}` : "";
  $("meta-status").textContent = campaign ? campaign.status : "";
}

function renderRoom(cell) {
  const root = $("room");
  root.replaceChildren();
  if (!cell) {
    root.textContent = "—";
    return;
  }

  const name = document.createElement("h3");
  name.textContent = cell.name;
  root.appendChild(name);

  if (cell.description) {
    const desc = document.createElement("p");
    desc.className = "room-desc";
    desc.textContent = cell.description;
    root.appendChild(desc);
  }

  const addList = (label, items, format) => {
    if (!items || items.length === 0) return;
    const heading = document.createElement("h4");
    heading.textContent = label;
    root.appendChild(heading);
    const list = document.createElement("ul");
    for (const item of items) {
      const li = document.createElement("li");
      li.textContent = format(item);
      list.appendChild(li);
    }
    root.appendChild(list);
  };

  addList("Exits", cell.exits, (e) => e);
  addList("Here", cell.characters, (c) =>
    `${c.name} — ${c.status}${c.disposition ? ` (${c.disposition})` : ""}`
  );
  addList("Items", cell.items, (i) => `${i.name} (${i.where})`);
  addList("Features", cell.features, (f) => {
    const bits = Object.entries(f.state || {}).map(([k, v]) => `${k}: ${v}`);
    return bits.length ? `${f.name} — ${bits.join(", ")}` : f.name;
  });
}

// ---------------------------------------------------------------------------
// Minimap (§17.3, §18)
//
// Only what the server returns is drawn: discovered cells (with walls derived
// from their exits) and rumoured cells (hatched, no exits). Everything else
// stays blank — the fog is the absence of data, not a client-side filter.
// NORTH is +y, so row 0 of the grid is y = height-1.
// ---------------------------------------------------------------------------

const DIRECTION_SIDES = {
  north: "top",
  south: "bottom",
  east: "right",
  west: "left",
};

function renderMinimap(map) {
  const root = $("minimap");
  root.replaceChildren();
  if (!map) return;

  root.style.setProperty("--cols", String(map.width));
  const byId = new Map(map.cells.map((cell) => [cell.cell_id, cell]));

  for (let row = 0; row < map.height; row += 1) {
    const y = map.height - 1 - row;
    for (let x = 0; x < map.width; x += 1) {
      const id = `cell_${x}_${y}`;
      const known = byId.get(id);
      const tile = document.createElement("div");
      tile.className = "tile";

      if (!known) {
        tile.classList.add("tile-unknown");
        tile.title = "unexplored";
      } else if (known.state === "RUMORED") {
        tile.classList.add("tile-rumored");
        tile.title = `${id} — rumoured`;
      } else {
        tile.classList.add("tile-known");
        // A side with no exit is a wall.
        for (const [direction, side] of Object.entries(DIRECTION_SIDES)) {
          if (!known.exits.includes(direction)) {
            tile.classList.add(`wall-${side}`);
          }
        }
        tile.title = known.name ? `${id} — ${known.name}` : id;
      }

      if (known && known.boss) {
        tile.classList.add("tile-boss");
        tile.textContent = "B";
      }
      if (id === map.player_cell) {
        tile.classList.add("tile-here");
        tile.textContent = "@";
        tile.title = `${tile.title} (you are here)`;
      }
      root.appendChild(tile);
    }
  }
}

// ---------------------------------------------------------------------------
// Character sheet and inventory (§18)
// ---------------------------------------------------------------------------

function meter(label, value, max, className) {
  const wrap = document.createElement("div");
  wrap.className = "meter";

  const head = document.createElement("div");
  head.className = "meter-head";
  const name = document.createElement("span");
  name.textContent = label;
  const amount = document.createElement("span");
  amount.textContent = `${value} / ${max}`;
  head.append(name, amount);

  const track = document.createElement("div");
  track.className = "meter-track";
  const fill = document.createElement("div");
  fill.className = `meter-fill ${className}`;
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  fill.style.width = `${pct}%`;
  track.appendChild(fill);

  wrap.append(head, track);
  return wrap;
}

function renderCharacter(sheet) {
  const root = $("character");
  root.replaceChildren();
  if (!sheet) {
    root.textContent = "—";
    return;
  }

  const title = document.createElement("h3");
  title.textContent = `${sheet.name} — level ${sheet.level}`;
  root.appendChild(title);

  const status = document.createElement("p");
  status.className = "char-status";
  status.textContent = `${sheet.status} · ${sheet.xp} xp · ${sheet.cell_id}`;
  root.appendChild(status);

  if (sheet.pending_level_ups > 0) {
    const pending = document.createElement("p");
    pending.className = "char-pending";
    pending.textContent = `${sheet.pending_level_ups} level-up available — type "level up"`;
    root.appendChild(pending);
  }

  root.appendChild(meter("HP", sheet.hp, sheet.max_hp, "fill-hp"));
  root.appendChild(meter("MP", sheet.mp, sheet.max_mp, "fill-mp"));

  const stats = document.createElement("dl");
  stats.className = "stats";
  const rows = [
    ["ATK", sheet.stats.attack],
    ["DEF", sheet.stats.defense],
    ["SPD", sheet.stats.speed],
    ["DODGE", `${sheet.stats.dodge_pct}%`],
    ["SKILL", sheet.stats.skill],
    ["KEYS", `${sheet.keys_held}/${sheet.keys_required}`],
  ];
  for (const [key, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = key;
    const dd = document.createElement("dd");
    dd.textContent = String(value);
    stats.append(dt, dd);
  }
  root.appendChild(stats);

  const equipHeading = document.createElement("h4");
  equipHeading.textContent = "Equipped";
  root.appendChild(equipHeading);
  const equipped = document.createElement("ul");
  equipped.className = "equipment";
  for (const [slot, item] of [["weapon", sheet.weapon], ["armor", sheet.armor]]) {
    const li = document.createElement("li");
    li.textContent = item ? `${slot}: ${item.name}` : `${slot}: —`;
    if (!item) li.classList.add("slot-empty");
    equipped.appendChild(li);
  }
  root.appendChild(equipped);

  const packHeading = document.createElement("h4");
  packHeading.textContent = `Carried (${sheet.carried.length}/${sheet.carried_slots})`;
  root.appendChild(packHeading);

  const pack = document.createElement("ul");
  pack.className = "inventory";
  for (let i = 0; i < sheet.carried_slots; i += 1) {
    const item = sheet.carried[i];
    const li = document.createElement("li");
    if (item) {
      li.textContent = item.quantity > 1 ? `${item.name} ×${item.quantity}` : item.name;
    } else {
      li.textContent = "empty";
      li.classList.add("slot-empty");
    }
    pack.appendChild(li);
  }
  root.appendChild(pack);
}

async function refreshPanels() {
  if (!state.campaignId) return;
  const base = `/api/campaigns/${state.campaignId}`;
  try {
    const [map, sheet] = await Promise.all([
      api("GET", `${base}/map?player_id=${encodeURIComponent(state.playerId)}`),
      api("GET", `${base}/player?player_id=${encodeURIComponent(state.playerId)}`),
    ]);
    renderMinimap(map);
    renderCharacter(sheet);
  } catch (error) {
    // Panels are a view of state, never the source of it: a failed refresh
    // must not interrupt play.
    toast(`Could not refresh panels: ${error.message}`);
  }
}

// ---------------------------------------------------------------------------
// Input gating — one turn in flight at a time (§18)
// ---------------------------------------------------------------------------

function setBusy(busy) {
  state.inFlight = busy;
  const enabled = !busy && Boolean(state.campaignId);
  $("input-box").disabled = !enabled;
  $("submit-btn").disabled = !enabled;
  $("submit-btn").textContent = busy ? "…" : "Act";
  if (enabled) $("input-box").focus();
}

// ---------------------------------------------------------------------------
// Campaign list / create / resume
// ---------------------------------------------------------------------------

async function refreshCampaigns(selectId) {
  const campaigns = await api("GET", "/api/campaigns");
  const select = $("campaign-select");
  select.replaceChildren();

  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "— no campaign —";
  select.appendChild(blank);

  for (const campaign of campaigns) {
    const option = document.createElement("option");
    option.value = campaign.campaign_id;
    option.textContent = `${campaign.player_name} · ${campaign.campaign_id} · turn ${campaign.current_turn}`;
    select.appendChild(option);
  }
  select.value = selectId || state.campaignId || "";
  return campaigns;
}

async function createCampaign() {
  const name = $("new-name").value.trim() || "Ada";
  setBusy(true);
  try {
    const created = await api("POST", "/api/campaigns", {
      player_name: name,
      seed: null,
    });
    state.campaignId = created.campaign.campaign_id;
    state.playerId = created.campaign.player_id;
    clearLog();
    logLine(`Campaign ${created.campaign.campaign_id} created.`, "system");
    applyTurnResult(created.initial);
    renderMeta(created.campaign);
    await refreshCampaigns(state.campaignId);
  } catch (error) {
    toast(`Could not create campaign: ${error.message}`);
  } finally {
    setBusy(false);
  }
}

async function resumeCampaign(campaignId) {
  const id = campaignId || $("campaign-select").value;
  if (!id) {
    toast("Pick a campaign first.");
    return;
  }
  setBusy(true);
  try {
    const resumed = await api("POST", `/api/campaigns/${id}/resume`, {
      player_id: state.playerId,
    });
    state.campaignId = id;
    state.playerId = resumed.campaign.player_id;
    clearLog();
    logLine(`Resumed ${id} from stored state — no transcript was replayed.`, "system");
    logLine(resumed.narration, "narration");
    renderRoom(resumed.visible_cell);
    renderMeta(resumed.campaign);
    window.dispatchEvent(new CustomEvent("campaign-refreshed"));
  } catch (error) {
    toast(`Could not resume: ${error.message}`);
  } finally {
    setBusy(false);
  }
}

// ---------------------------------------------------------------------------
// Turns
// ---------------------------------------------------------------------------

function applyTurnResult(result) {
  logLine(result.narration, result.accepted ? "narration" : "rejected");
  if (result.narration_source === "TEMPLATE") {
    logLine("(narration fell back to template text)", "system");
  }
  renderRoom(result.visible_cell);
  $("meta-turn").textContent = `turn ${result.turn_sequence}`;
  $("meta-status").textContent = result.campaign_status;
  window.dispatchEvent(new CustomEvent("campaign-refreshed"));
}

async function submitTurn(text) {
  if (!state.campaignId || state.inFlight) return;

  // Reuse the pending id when the previous attempt failed, so the server sees
  // a retry of the same turn rather than a new one.
  const turnId =
    state.pendingTurnId && state.pendingInput === text
      ? state.pendingTurnId
      : crypto.randomUUID();
  state.pendingTurnId = turnId;
  state.pendingInput = text;

  logLine(`> ${text}`, "player");
  setBusy(true);
  try {
    const result = await api(
      "POST",
      `/api/campaigns/${state.campaignId}/turns`,
      { turn_id: turnId, player_id: state.playerId, input: text }
    );
    state.pendingTurnId = null;
    state.pendingInput = null;
    applyTurnResult(result);
  } catch (error) {
    if (error.status === 409) {
      toast("The campaign moved on; press Act again to retry this turn.");
    } else {
      toast(`Turn failed: ${error.message}. Retry sends the same turn id.`);
    }
    logLine(`(turn not applied: ${error.message})`, "system");
  } finally {
    setBusy(false);
  }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function init() {
  $("create-btn").addEventListener("click", createCampaign);
  $("resume-btn").addEventListener("click", () => resumeCampaign());
  $("campaign-select").addEventListener("change", (event) => {
    if (event.target.value) resumeCampaign(event.target.value);
  });
  $("input-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const box = $("input-box");
    const text = box.value.trim();
    if (!text) return;
    box.value = "";
    submitTurn(text);
  });

  window.addEventListener("campaign-refreshed", refreshPanels);

  refreshCampaigns().catch(() => toast("Could not reach the server."));
  logLine(
    "Create a campaign or resume an existing one. State lives in the database, not in this page.",
    "system"
  );
}

document.addEventListener("DOMContentLoaded", init);
