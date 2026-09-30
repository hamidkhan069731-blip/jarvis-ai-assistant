// J.A.R.V.I.S. console — application entry point.
// Wires the WebSocket core, the orb, the voice pipeline, and the DOM together.

import { WSClient } from "./ws.js";
import { Orb } from "./orb.js";
import { Voice } from "./voice.js";
import { UI, $, $$ } from "./ui.js";

const ui = new UI();
const orb = new Orb($("#orb"), $("#orb-state"));
const wsURL = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";
const ws = new WSClient(wsURL);

const state = {
  conversationId: null,
  settings: {},
  busy: false,
  rest: "idle",          // last authoritative resting state (idle/standby/watching/offline/error)
  pendingApproval: null,
};

// ------------------------------------------------------------------ //
// Voice pipeline
// ------------------------------------------------------------------ //
const voice = new Voice({
  result: (text, isFinal) => {
    $("#input").value = text;
    if (isFinal) { sendChat(text); $("#input").value = ""; }
  },
  listening: (on) => {
    $("#btn-mic").classList.toggle("listening", on);
    if (on) { orb.setState("listening"); voice.cancelSpeech(); }
    else if (!state.busy) orb.setState(state.rest);
  },
  level: (v) => orb.setLevel(v),
  speaking: (on) => { if (on) orb.setState("speaking"); else if (!state.busy) orb.setState(state.rest); },
  wake: () => { ui.toast("Wake word detected — listening…", "info"); },
  error: (msg) => ui.toast(msg, "error"),
});

function speakReply(text) {
  if (state.settings.tts_enabled === false) return;
  if (state.settings.server_tts) {
    fetch("/api/speak", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).catch(() => {});
  } else {
    voice.speak(text);
  }
}

// ------------------------------------------------------------------ //
// WebSocket handlers
// ------------------------------------------------------------------ //
ws.onStatus((s) => ui.setConnection(s));

ws.on("hello", (m) => {
  ui.hideBoot();
  applySettings(m.settings || {});
  if (m.diagnostics) {
    ui.updateDiagnostics(m.diagnostics);
    if (m.diagnostics.provider) ui.setProvider(m.diagnostics.provider.name, m.diagnostics.provider.mode);
  }
  refreshPanels();
  loadSkills();
});

ws.on("state", (m) => {
  const s = m.state;
  // states in which JARVIS is NOT actively working a turn
  const restful = ["idle", "error", "standby", "watching", "offline"];
  state.busy = !restful.includes(s);
  if (restful.includes(s)) state.rest = s;   // remember where to return after speaking/listening ends
  $("#btn-stop").hidden = !state.busy;
  if (s === "thinking" || s === "planning") { orb.setState(s); ui.showTyping(); }
  else if (s === "executing") { orb.setState("executing"); }
  else if (s === "waiting_approval") { orb.setState("waiting_approval"); }
  else if (s === "standby") { orb.setState("standby"); ui.hideTyping(); }
  else if (s === "watching") { orb.setState("watching"); ui.hideTyping(); }
  else if (s === "offline") { orb.setState("offline"); ui.hideTyping(); }
  else if (s === "error") { orb.setState("error"); ui.hideTyping(); }
  else { if (!voice.speaking) orb.setState(state.rest); ui.hideTyping(); }
});

ws.on("message", (m) => {
  if (m.role !== "assistant") return;
  state.conversationId = m.conversation_id ?? state.conversationId;
  const label = m.mode === "local" ? "local" : `${m.provider} · ${m.mode}`;
  ui.addMessage("assistant", m.content, label);
  if (m.speak) speakReply(m.content);
  refreshPanels();
});

ws.on("tool_status", (m) => ui.pushActivity(m));

ws.on("approval_request", (m) => {
  state.pendingApproval = m.approval_id;
  ui.showApproval(m);
});
ws.on("approval_timeout", () => {
  state.pendingApproval = null;
  ui.hideApproval();
  ui.toast("Approval timed out — action was declined.", "warning");
});
// Settled elsewhere (JARVIS open on a second screen, or the same account in
// another window). Dismiss our copy rather than leave a modal asking about
// something that has already happened.
ws.on("approval_resolved", (m) => {
  if (state.pendingApproval !== m.approval_id) return;
  state.pendingApproval = null;
  ui.hideApproval();
  ui.toast(m.approved ? "Approved on another screen." : "Denied on another screen.",
           m.approved ? "info" : "warning");
});

ws.on("notification", (m) => ui.toast(m.message, m.level || "info"));
ws.on("task_update", (m) => { if (m.task) ui.upsertTask(m.task); });
ws.on("system_stats", (m) => ui.updateStats(m.stats));
ws.on("settings_changed", (m) => { applySettings(m.settings || {}); });

// ------------------------------------------------------------------ //
// Sending
// ------------------------------------------------------------------ //
function sendChat(text) {
  text = (text || "").trim();
  if (!text) return;
  voice.cancelSpeech();
  ui.addMessage("user", text);
  ws.send({ type: "chat", text, conversation_id: state.conversationId });
}

$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#input");
  sendChat(input.value);
  input.value = "";
});

$("#btn-stop").addEventListener("click", () => {
  ws.send({ type: "stop" });
  voice.cancelSpeech();
  ui.hideTyping();
  orb.setState("idle");
});

// quick-action chips
$$("#quick-actions .chip").forEach((chip) =>
  chip.addEventListener("click", () => sendChat(chip.dataset.say)));

// ------------------------------------------------------------------ //
// Push-to-talk (button hold + Ctrl+Space)
// ------------------------------------------------------------------ //
const mic = $("#btn-mic");
let micHeld = false;
const micDown = () => { if (!micHeld) { micHeld = true; voice.startCommand(); } };
const micUp = () => { if (micHeld) { micHeld = false; voice.stopCommand(); } };
mic.addEventListener("mousedown", micDown);
mic.addEventListener("mouseup", micUp);
mic.addEventListener("mouseleave", micUp);
mic.addEventListener("touchstart", (e) => { e.preventDefault(); micDown(); }, { passive: false });
mic.addEventListener("touchend", (e) => { e.preventDefault(); micUp(); });
// click (no hold) toggles a single capture for accessibility
let clickToggle = false;
mic.addEventListener("click", () => {
  if (micHeld) return;
  clickToggle = !clickToggle;
  clickToggle ? voice.startCommand() : voice.stopCommand();
});

document.addEventListener("keydown", (e) => {
  if (e.ctrlKey && e.code === "Space") { e.preventDefault(); micDown(); }
  if (e.key === "Escape" && state.busy) { ws.send({ type: "stop" }); }
});
document.addEventListener("keyup", (e) => {
  if (e.code === "Space" && micHeld) micUp();
});

// ------------------------------------------------------------------ //
// Tabs
// ------------------------------------------------------------------ //
$$(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    $$(".tab-panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(`.tab-panel[data-panel="${tab.dataset.tab}"]`).classList.add("active");
    if (tab.dataset.tab === "audit") loadAudit();
    if (tab.dataset.tab === "skills") loadSkills();
  }));

// ------------------------------------------------------------------ //
// Approval modal
// ------------------------------------------------------------------ //
$("#appr-approve").addEventListener("click", () => resolveApproval(true));
$("#appr-deny").addEventListener("click", () => resolveApproval(false));
function resolveApproval(approved) {
  if (!state.pendingApproval) return;
  ws.send({
    type: "approve", approval_id: state.pendingApproval,
    approved, remember: $("#appr-remember").checked,
  });
  state.pendingApproval = null;
  ui.hideApproval();
}

// ------------------------------------------------------------------ //
// Memory + task cancel (event delegation)
// ------------------------------------------------------------------ //
$("#mem-add").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#mem-input");
  const value = input.value.trim();
  if (!value) return;
  await fetch("/api/memory", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "fact", value }),
  });
  input.value = "";
  loadMemory();
});

document.body.addEventListener("click", async (e) => {
  const memId = e.target.closest("[data-mem]")?.dataset.mem;
  if (memId) { await fetch(`/api/memory/${memId}`, { method: "DELETE" }); loadMemory(); }
  const taskId = e.target.closest("[data-cancel]")?.dataset.cancel;
  if (taskId) ws.send({ type: "cancel_task", task_id: Number(taskId) });
});

// ------------------------------------------------------------------ //
// Settings drawer
// ------------------------------------------------------------------ //
$("#btn-settings").addEventListener("click", () => ui.openSettings());
$("#settings-close").addEventListener("click", () => ui.closeSettings());
$("#settings").addEventListener("click", (e) => { if (e.target.id === "settings") ui.closeSettings(); });
$("#set-provider").addEventListener("change", () => ui.populateSettings({ ...state.settings, ai_provider: $("#set-provider").value }));
$("#set-rate").addEventListener("input", () => { $("#rate-out").textContent = $("#set-rate").value; });
$("#set-wake-on").addEventListener("change", () => voice.configure({ wakeEnabled: $("#set-wake-on").checked }));

$("#settings-save").addEventListener("click", async () => {
  const payload = {
    ai_provider: $("#set-provider").value,
    permission_mode: $("#set-permission").value,
    enable_shell: $("#set-shell").checked,
    tts_enabled: $("#set-tts").checked,
    server_tts: $("#set-server-tts").checked,
    wake_word: $("#set-wake").value.trim().toLowerCase() || "hey jarvis",
    tts_rate: Number($("#set-rate").value),
    memory_enabled: $("#set-memory").checked,
    proactive_enabled: $("#set-proactive").checked,
  };
  const res = await fetch("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  applySettings(data.settings || {});
  // wake toggle is browser-side
  voice.configure({ wakeEnabled: $("#set-wake-on").checked, wakeWord: payload.wake_word });
  ui.flashSaved();
  ui.toast("Settings saved.", "info");
});

// language toggle (EN <-> UR)
const langBtn = $("#btn-mic-lang");
langBtn.addEventListener("click", () => {
  const next = langBtn.textContent === "EN" ? "UR" : "EN";
  langBtn.textContent = next;
  voice.setLang(next === "UR" ? "ur-PK" : "en-US");
  ui.toast(`Recognition language: ${next === "UR" ? "Urdu" : "English"}`, "info");
});

// ------------------------------------------------------------------ //
// Settings application + REST panel refresh
// ------------------------------------------------------------------ //
function applySettings(s) {
  state.settings = { ...state.settings, ...s };
  ui.populateSettings(state.settings);
  if (s.ai_provider) ui.setProvider(s.ai_provider, s.ai_provider === "local" ? "local" : "cloud");
  voice.configure({
    wakeWord: state.settings.wake_word,
    rate: state.settings.tts_rate,
    volume: state.settings.tts_volume,
    voice: state.settings.tts_voice,
  });
  // reflect diagnostics that depend on settings
  const permEl = document.querySelector('[data-diag="permission_mode"]');
  if (permEl && state.settings.permission_mode) permEl.textContent = state.settings.permission_mode;
}

async function refreshPanels() { loadTasks(); loadMemory(); }

async function loadTasks() {
  try { const r = await (await fetch("/api/tasks")).json(); ui.renderTasks(r.tasks); } catch {}
}
async function loadMemory() {
  try { const r = await (await fetch("/api/memory")).json(); ui.renderMemory(r.memory); } catch {}
}
async function loadAudit() {
  try { const r = await (await fetch("/api/audit")).json(); ui.renderAudit(r.audit); } catch {}
}
async function loadSkills() {
  try { const r = await (await fetch("/api/skills")).json(); ui.renderSkills(r.skills); } catch {}
}

// ------------------------------------------------------------------ //
// Boot
// ------------------------------------------------------------------ //
ui.bootStatus("connecting to core…");
ws.connect();

// Report voice capability honestly once the DOM is live.
if (!voice.sttSupported) {
  ui.toast("Voice input needs Chrome or Edge (Web Speech API). Text input works everywhere.", "warning");
}

// Safety: if the core never says hello (e.g. static preview), lift the boot veil.
setTimeout(() => { if ($("#boot")) ui.hideBoot(); }, 4000);
