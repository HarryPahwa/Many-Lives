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

  refreshCampaigns().catch(() => toast("Could not reach the server."));
  logLine(
    "Create a campaign or resume an existing one. State lives in the database, not in this page.",
    "system"
  );
}

document.addEventListener("DOMContentLoaded", init);
