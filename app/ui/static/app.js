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
  // Mirrors TurnResult.debug_available: false when DEBUG_ENDPOINTS is off.
  debugAvailable: false,
  lastTurnId: null,
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
  if (speech.enabled) unlockAudio();
  cancelSpeech();
  setBusy(true);
  try {
    const created = await api("POST", "/api/campaigns", {
      player_name: name,
      seed: null,
    });
    state.campaignId = created.campaign.campaign_id;
    state.playerId = created.campaign.player_id;
    rememberCampaign(state.campaignId);
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

async function resumeCampaign(campaignId, fromGesture = true) {
  const id = campaignId || $("campaign-select").value;
  if (!id) {
    toast("Pick a campaign first.");
    return;
  }
  if (fromGesture && speech.enabled) unlockAudio();
  cancelSpeech();
  setBusy(true);
  try {
    const resumed = await api("POST", `/api/campaigns/${id}/resume`, {
      player_id: state.playerId,
    });
    state.campaignId = id;
    state.playerId = resumed.campaign.player_id;
    state.debugAvailable = Boolean(resumed.debug_available);
    state.lastTurnId = null; // the resume record is the latest turn
    $("inspector-panel").hidden = !state.debugAvailable;
    rememberCampaign(id);
    clearLog();
    logLine(`Resumed ${id} from stored state — no transcript was replayed.`, "system");
    logLine(resumed.narration, "narration");
    speak(resumed.narration, fromGesture);
    renderRoom(resumed.visible_cell);
    renderMeta(resumed.campaign);
    window.dispatchEvent(new CustomEvent("campaign-refreshed"));
    refreshInspector(null);
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
  state.debugAvailable = Boolean(result.debug_available);
  state.lastTurnId = result.turn_id;
  $("inspector-panel").hidden = !state.debugAvailable;
  logLine(result.narration, result.accepted ? "narration" : "rejected");
  speak(result.narration, true);
  if (result.narration_source === "TEMPLATE") {
    logLine("(narration fell back to template text)", "system");
  }
  logTurnTrace(result);
  renderRoom(result.visible_cell);
  $("meta-turn").textContent = `turn ${result.turn_sequence}`;
  $("meta-status").textContent = result.campaign_status;
  window.dispatchEvent(new CustomEvent("campaign-refreshed"));
  refreshInspector(result.turn_id);
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
  if (speech.enabled) unlockAudio();
  cancelSpeech();
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

// ---------------------------------------------------------------------------
// Context inspector (§9.7, §17.2, §18)
//
// The demo's point: the model is handed exact state, a bounded window of
// recent events, and a few retrieved memories — not the whole history. This
// panel shows precisely what went into the last turn, sourced from the `turns`
// record rather than from anything the page kept.
// ---------------------------------------------------------------------------

function kv(parent, label, value) {
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value === null || value === undefined ? "—" : String(value);
  parent.append(dt, dd);
}

function inspectorSection(root, title) {
  const heading = document.createElement("h4");
  heading.textContent = title;
  root.appendChild(heading);
  return heading;
}

function idList(root, ids) {
  const list = document.createElement("ul");
  list.className = "id-list";
  if (!ids || ids.length === 0) {
    const li = document.createElement("li");
    li.textContent = "none";
    li.className = "slot-empty";
    list.appendChild(li);
  } else {
    for (const id of ids) {
      const li = document.createElement("li");
      li.textContent = id;
      list.appendChild(li);
    }
  }
  root.appendChild(list);
}

function renderInspector(debug) {
  const root = $("inspector");
  root.replaceChildren();
  if (!debug) {
    root.textContent = "No turn inspected yet.";
    return;
  }

  const summary = document.createElement("dl");
  summary.className = "inspector-kv";
  kv(summary, "turn", debug.turn_id);
  kv(summary, "kind", debug.kind);
  kv(summary, "status", debug.status);
  kv(summary, "path", debug.path);
  kv(summary, "class", debug.action_class);
  root.appendChild(summary);

  const manifest = debug.context_manifest;
  inspectorSection(root, "Context manifest");
  if (!manifest) {
    const none = document.createElement("p");
    none.textContent = "Fast path — no context was built and no model was asked.";
    none.className = "slot-empty";
    root.appendChild(none);
  } else {
    const mdl = document.createElement("dl");
    mdl.className = "inspector-kv";
    kv(mdl, "policy", `v${manifest.policy_version}`);
    kv(mdl, "est. tokens", manifest.estimated_tokens);
    kv(mdl, "components", manifest.components.join(", ") || "—");
    root.appendChild(mdl);

    inspectorSection(root, `Entities (${manifest.entity_ids.length})`);
    idList(root, manifest.entity_ids);

    inspectorSection(root, `Recent events (${manifest.event_ids.length})`);
    idList(root, manifest.event_ids);

    inspectorSection(root, `Memories (${manifest.memories.length})`);
    const memories = document.createElement("ul");
    memories.className = "id-list";
    if (manifest.memories.length === 0) {
      const li = document.createElement("li");
      li.textContent = "none retrieved";
      li.className = "slot-empty";
      memories.appendChild(li);
    } else {
      for (const memory of manifest.memories) {
        const li = document.createElement("li");
        const score = Number(memory.score).toFixed(2);
        li.textContent = memory.text
          ? `${memory.id} (${score}) — ${memory.text}`
          : `${memory.id} (${score})`;
        memories.appendChild(li);
      }
    }
    root.appendChild(memories);

    if (manifest.notes && manifest.notes.length) {
      const notes = document.createElement("p");
      notes.className = "inspector-note";
      notes.textContent = manifest.notes.join(" · ");
      root.appendChild(notes);
    }
  }

  inspectorSection(root, "Proposal");
  const proposal = document.createElement("pre");
  proposal.className = "inspector-json";
  proposal.textContent = debug.proposal
    ? JSON.stringify(debug.proposal, null, 2)
    : "none — the fast path skipped the adjudicator";
  root.appendChild(proposal);

  inspectorSection(root, "Effects");
  const effects = document.createElement("dl");
  effects.className = "inspector-kv";
  kv(effects, "accepted", debug.accepted_effect_types.join(", ") || "none");
  kv(effects, "rejected", debug.rejected_effects.length || "none");
  kv(effects, "events", debug.event_ids.join(", ") || "none");
  root.appendChild(effects);

  inspectorSection(root, "Claims and verification");
  const claims = document.createElement("ul");
  claims.className = "id-list";
  if (debug.claims.length === 0) {
    const li = document.createElement("li");
    li.textContent = "no claims";
    li.className = "slot-empty";
    claims.appendChild(li);
  } else {
    for (const claim of debug.claims) {
      const li = document.createElement("li");
      li.textContent = `${claim.entity_id}.${claim.attribute} = ${claim.value} [${claim.verdict}]`;
      claims.appendChild(li);
    }
  }
  root.appendChild(claims);

  if (debug.verification) {
    const verification = document.createElement("dl");
    verification.className = "inspector-kv";
    kv(verification, "checked", debug.verification.claims_checked);
    kv(verification, "contradictions", debug.verification.contradictions);
    kv(verification, "unknown entities", debug.verification.unknown_entities);
    root.appendChild(verification);
  }

  inspectorSection(root, "Model calls");
  const calls = document.createElement("ul");
  calls.className = "id-list";
  if (debug.model_calls.length === 0) {
    const li = document.createElement("li");
    li.textContent = "none — resolved entirely in code";
    li.className = "slot-empty";
    calls.appendChild(li);
  } else {
    for (const call of debug.model_calls) {
      const li = document.createElement("li");
      li.textContent =
        `${call.role} · ${call.model} · ${call.latency_ms}ms · ` +
        `${call.input_tokens}→${call.output_tokens} tok · ${call.attempts} attempt(s)`;
      calls.appendChild(li);
    }
  }
  root.appendChild(calls);

  if (debug.vector_search_ms !== null && debug.vector_search_ms !== undefined) {
    const vector = document.createElement("p");
    vector.className = "inspector-note";
    vector.textContent = `vector search: ${debug.vector_search_ms}ms`;
    root.appendChild(vector);
  }
}

// A compact "what the harness did" block under each turn: routing, the
// adjudicator's proposal, and the engine's verdict.
async function logTurnTrace(result) {
  const entry = document.createElement("div");
  entry.className = "log-entry log-trace";
  const row = (label, value) => {
    const line = document.createElement("div");
    const key = document.createElement("span");
    key.className = "trace-key";
    key.textContent = `${label}:`;
    line.append(key, ` ${value}`);
    entry.appendChild(line);
  };

  let debug = null;
  if (state.debugAvailable) {
    try {
      debug = await api(
        "GET",
        `/api/campaigns/${state.campaignId}/debug/context?turn_id=${encodeURIComponent(result.turn_id)}`
      );
    } catch {
      debug = null;
    }
  }

  if (debug) {
    const route = debug.path === "FAST" ? "FAST (parser, no model)" : debug.path;
    row("route", debug.action_class ? `${route} · class ${debug.action_class}` : route);
    const p = debug.proposal;
    if (p) {
      const targets = (p.targets || []).join(", ") || "none";
      row("proposal", `${p.action_type} → ${targets} · ${p.feasibility}`);
      const effects = (p.proposed_effects_on_success || [])
        .map((e) => [e.type, e.feature_id || e.entity_id || e.item_id, e.key && `${e.key}=${e.value}`]
          .filter(Boolean).join(" "))
        .join("; ");
      if (effects) row("proposed", effects);
      if (p.reason) row("model reason", p.reason);
    }
  }

  const verdict = result.accepted ? "ACCEPTED" : `REJECTED — ${result.reason || "no reason"}`;
  row("engine", debug && debug.reason_code && !result.accepted
    ? `${verdict} [${debug.reason_code}]` : verdict);
  const events = (result.outcome && result.outcome.events) || [];
  row("events", events.length ? events.join(", ") : "none (no state change)");
  if (result.accepted && result.outcome && result.outcome.summary) {
    row("last event", result.outcome.summary);
  }
  const rolls = (result.outcome && result.outcome.rolls) || [];
  if (rolls.length) {
    row("rolls", rolls.map((r) => `${r.purpose} d${r.sides}=${r.value}`).join(", "));
  }
  if (debug && debug.rejected_effects && debug.rejected_effects.length) {
    row("dropped effects", debug.rejected_effects.map((e) => e.type).join(", "));
  }
  if (debug && debug.model_calls && debug.model_calls.length) {
    row("model calls", debug.model_calls
      .map((c) => `${c.role.toLowerCase()} ${c.latency_ms}ms`).join(", "));
  }

  const log = $("narrative");
  log.appendChild(entry);
  log.scrollTop = log.scrollHeight;
}

async function refreshInspector(turnId) {
  if (!state.campaignId || !state.debugAvailable) return;
  // Only fetch when the panel is actually open: the inspector is a demo
  // affordance, not something to pay for on every turn.
  if (!$("inspector-details").open) return;
  try {
    const query = turnId ? `?turn_id=${encodeURIComponent(turnId)}` : "";
    const debug = await api(
      "GET",
      `/api/campaigns/${state.campaignId}/debug/context${query}`
    );
    renderInspector(debug);
  } catch (error) {
    renderInspector(null);
    $("inspector").textContent = `Inspector unavailable: ${error.message}`;
  }
}

// ---------------------------------------------------------------------------
// Restart / reconnect (§18, §29.2)
//
// The demo kills the server from the terminal. The page survives that, so on
// load it reconnects by resuming the campaign it was last on. The id is the
// only thing kept locally — never any game state, which would defeat the
// point: continuity has to come from the store.
// ---------------------------------------------------------------------------

const LAST_CAMPAIGN_KEY = "many-lives:last-campaign";
const SPEECH_KEY = "many-lives:speak";

// Narration audio. The page sends text to /api/speech; the ElevenLabs key
// stays on the server. Playback uses an AudioContext unlocked during the
// click that started the turn, so the clip can start after the request returns.
const speech = {
  enabled: false,
  context: null,
  source: null,
  lastText: "",
  requestId: 0,
  abort: null,
};

function speechPreference(enabled) {
  try {
    window.localStorage.setItem(SPEECH_KEY, enabled ? "on" : "off");
  } catch {
    // A blocked store must not stop playback for this page load.
  }
}

function loadSpeechPreference() {
  try {
    // Only an explicit "on" enables speech. A missing key stays off, so a
    // fresh page never calls ElevenLabs.
    speech.enabled = window.localStorage.getItem(SPEECH_KEY) === "on";
  } catch {
    speech.enabled = false;
  }
  const toggle = $("speech-toggle");
  if (toggle) toggle.checked = speech.enabled;
}

function setSpeechStatus(text) {
  const status = $("speech-status");
  if (status) status.textContent = text;
}

function unlockAudio() {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return null;
  if (!speech.context) speech.context = new Ctx();
  if (speech.context.state === "suspended") speech.context.resume();
  return speech.context;
}

function haltPlayback() {
  if (!speech.source) return;
  try {
    speech.source.stop();
  } catch {
    // The source already ended.
  }
  speech.source = null;
}

function cancelSpeech() {
  speech.requestId += 1;
  if (speech.abort) {
    speech.abort.abort();
    speech.abort = null;
  }
  haltPlayback();
  setSpeechStatus("");
}

async function speak(text, fromGesture) {
  const clean = (text || "").trim();
  if (!clean) return;
  speech.lastText = clean;
  const replay = $("speech-replay");
  if (replay) replay.disabled = false;
  if (!speech.enabled) return;

  const ctx = speech.context;
  if (!fromGesture && (!ctx || ctx.state !== "running")) {
    setSpeechStatus("Press Replay to hear this.");
    return;
  }
  if (!ctx) {
    setSpeechStatus("This browser cannot play audio.");
    return;
  }

  if (!speech.enabled) return;
  const requestId = ++speech.requestId;
  haltPlayback();
  const abort = new AbortController();
  speech.abort = abort;
  setSpeechStatus("Speaking…");
  try {
    const response = await fetch("/api/speech", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "audio/mpeg" },
      body: JSON.stringify({ text: clean }),
      signal: abort.signal,
    });
    if (requestId !== speech.requestId) return;
    if (!response.ok) {
      setSpeechStatus("");
      let message = "Could not speak that narration.";
      try {
        const payload = await response.json();
        if (payload && payload.error && payload.error.message) {
          message = payload.error.message;
        }
      } catch {
        // The body was not the error envelope.
      }
      if (response.status === 503) {
        // Not configured. Stop asking this session; a reload tries again.
        speech.enabled = false;
        const toggle = $("speech-toggle");
        if (toggle) toggle.checked = false;
      }
      toast(message);
      return;
    }
    const bytes = await response.arrayBuffer();
    if (requestId !== speech.requestId || !speech.enabled) return;
    if (ctx.state === "suspended") await ctx.resume();
    const buffer = await ctx.decodeAudioData(bytes.slice(0));
    if (requestId !== speech.requestId || !speech.enabled) return;
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    source.onended = () => {
      if (speech.source === source) {
        speech.source = null;
        setSpeechStatus("");
      }
    };
    speech.source = source;
    source.start();
  } catch (error) {
    if (requestId !== speech.requestId || error.name === "AbortError") return;
    haltPlayback();
    setSpeechStatus("Press Replay to hear this.");
    toast(`Could not speak that narration: ${error.message}`);
  }
}

function rememberCampaign(campaignId) {
  try {
    window.localStorage.setItem(LAST_CAMPAIGN_KEY, campaignId);
  } catch {
    // Private mode or blocked storage: reconnecting is a convenience, and
    // losing it must never break play.
  }
}

function lastCampaign() {
  try {
    return window.localStorage.getItem(LAST_CAMPAIGN_KEY);
  } catch {
    return null;
  }
}

async function reconnect() {
  const campaigns = await refreshCampaigns();
  const remembered = lastCampaign();
  if (!remembered) return;
  if (!campaigns.some((c) => c.campaign_id === remembered)) {
    // The server no longer knows it — a stub restart, or another database.
    logLine(
      `Campaign ${remembered} is no longer on the server. Pick another or create one.`,
      "system"
    );
    return;
  }
  logLine("Reconnecting to the last campaign…", "system");
  await resumeCampaign(remembered, false);
}

const TRACE_KEY = "many-lives:trace";

function applyTracePreference(visible) {
  document.body.classList.toggle("hide-trace", !visible);
  $("trace-toggle").checked = visible;
  try {
    window.localStorage.setItem(TRACE_KEY, visible ? "on" : "off");
  } catch {
    // A blocked store only means the choice is not remembered.
  }
}

function init() {
  loadSpeechPreference();
  let traceVisible = true;
  try {
    traceVisible = window.localStorage.getItem(TRACE_KEY) !== "off";
  } catch {
    traceVisible = true;
  }
  applyTracePreference(traceVisible);
  $("trace-toggle").addEventListener("change", (event) => {
    applyTracePreference(event.target.checked);
  });
  $("speech-toggle").addEventListener("change", (event) => {
    speech.enabled = event.target.checked;
    speechPreference(speech.enabled);
    if (!speech.enabled) cancelSpeech();
  });
  $("speech-replay").addEventListener("click", () => {
    if (!speech.lastText) return;
    speech.enabled = true;
    $("speech-toggle").checked = true;
    speechPreference(true);
    unlockAudio();
    speak(speech.lastText, true);
  });
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
  $("inspector-details").addEventListener("toggle", () => {
    if ($("inspector-details").open) refreshInspector(state.lastTurnId);
  });

  logLine(
    "Create a campaign or resume an existing one. State lives in the store, not in this page.",
    "system"
  );
  reconnect().catch((error) => {
    toast(`Could not reach the server: ${error.message}`);
    logLine(
      "Server unreachable. Start it again, then reload this page to reconnect.",
      "system"
    );
  });
}

document.addEventListener("DOMContentLoaded", init);
