// DOM rendering layer. Pure view helpers over the JARVIS console markup.
// app.js owns state and wiring; this module only touches the DOM.

import { renderMarkdown } from "./markdown.js";

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const RING_LEN = 2 * Math.PI * 34; // circumference of gauge ring (r=34)

function fmtTime(epochSeconds) {
  if (!epochSeconds) return "";
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export class UI {
  constructor() {
    this.conversation = $("#conversation");
    this._typingEl = null;
    this._tasks = new Map();
  }

  // ---------------- boot ---------------- //
  bootStatus(text) { const el = $("#boot-status"); if (el) el.textContent = text; }
  hideBoot() { const b = $("#boot"); if (b) { b.classList.add("hide"); setTimeout(() => b.remove(), 700); } }

  // ---------------- connection ---------------- //
  setConnection(status) {
    const badge = $("#conn-badge"), text = $("#conn-text");
    text.textContent = status;
    badge.className = "badge " + (
      status === "connected" ? "badge-ok" :
      status === "connecting" ? "badge-warn" : "badge-err");
  }
  setProvider(name, mode) {
    const b = $("#provider-badge");
    b.textContent = mode === "local" ? "local" : `${name}`;
    b.title = `AI brain: ${name} (${mode})`;
  }

  // ---------------- conversation ---------------- //
  addMessage(role, content, meta = null) {
    this.hideTyping();
    const wrap = document.createElement("div");
    wrap.className = `msg ${role}`;
    const avatar = document.createElement("div");
    avatar.className = "msg-avatar";
    avatar.textContent = role === "user" ? "You" : role === "assistant" ? "J" : "•";
    const body = document.createElement("div");
    body.className = "msg-body";
    body.innerHTML = role === "assistant" ? renderMarkdown(content)
                    : `<p>${escapeText(content)}</p>`;
    if (meta) {
      const m = document.createElement("div");
      m.className = "msg-meta";
      m.textContent = meta;
      body.appendChild(m);
    }
    wrap.append(avatar, body);
    this.conversation.appendChild(wrap);
    this._scroll();
    return wrap;
  }

  showTyping() {
    if (this._typingEl) return;
    const wrap = document.createElement("div");
    wrap.className = "msg assistant";
    wrap.innerHTML = `<div class="msg-avatar">J</div>
      <div class="msg-body"><span class="typing"><i></i><i></i><i></i></span></div>`;
    this.conversation.appendChild(wrap);
    this._typingEl = wrap;
    this._scroll();
  }
  hideTyping() { if (this._typingEl) { this._typingEl.remove(); this._typingEl = null; } }

  _scroll() { this.conversation.scrollTop = this.conversation.scrollHeight; }

  // ---------------- system gauges ---------------- //
  updateStats(s) {
    if (!s || !s.available) return;
    this._gauge("cpu", s.cpu_percent);
    this._gauge("ram", s.ram_percent);
    this._gauge("disk", s.disk_percent);
    this._field("ram_detail", `${s.ram_used_gb ?? "–"} / ${s.ram_total_gb ?? "–"} GB`);
    this._field("disk_detail", `${s.disk_free_gb ?? "–"} GB free`);
    this._field("net_detail", s.net_recv_mb != null
      ? `↓${s.net_recv_mb} ↑${s.net_sent_mb} MB` : "–");
    this._field("battery_detail", s.battery_percent != null
      ? `${s.battery_percent}% ${s.battery_plugged ? "(charging)" : ""}` : "n/a");
    this._field("proc_detail", s.process_count ?? "–");
    this._field("uptime_detail", s.uptime_hours != null ? `${s.uptime_hours} h` : "–");
  }

  _gauge(metric, pct) {
    if (pct == null) return;
    const g = $(`.gauge[data-metric="${metric}"]`);
    if (!g) return;
    const fg = $(".ring-fg", g);
    const p = Math.max(0, Math.min(100, pct));
    fg.style.strokeDasharray = RING_LEN.toFixed(1);
    fg.style.strokeDashoffset = (RING_LEN * (1 - p / 100)).toFixed(1);
    g.classList.toggle("warn", p >= 75 && p < 90);
    g.classList.toggle("crit", p >= 90);
    this._field(metric, Math.round(p));
  }
  _field(name, val) {
    const el = $(`[data-field="${name}"]`);
    if (el) el.textContent = val;
  }

  updateDiagnostics(d) {
    const set = (k, v) => { const el = $(`[data-diag="${k}"]`); if (el) el.textContent = v; };
    if (d.provider) set("provider", d.provider.name || "–");
    if (d.provider) set("mode", d.provider.mode || "–");
    if (d.tools != null) set("tools", d.tools);
    if (d.skills != null) set("skills", d.skills);
    if (d.tts) set("tts", d.tts.available ? "ready" : "unavailable");
    if (d.stt) set("stt", d.stt.microphone_available ? "server+browser" : "browser");
    if (d.permission_mode) set("permission_mode", d.permission_mode);
  }

  // ---------------- activity feed ---------------- //
  pushActivity(evt) {
    const feed = $("#activity-feed");
    const el = document.createElement("div");
    const status = evt.status || "info";
    el.className = `event ${status}`;
    const args = evt.arguments ? ` <span class="ev-time">${escapeText(shortArgs(evt.arguments))}</span>` : "";
    el.innerHTML = `
      <div class="ev-head">
        <span class="ev-tool">${escapeText(evt.tool || "event")}</span>
        <span class="status-tag ${status}">${status}</span>
      </div>
      ${evt.summary ? `<div class="ev-sum">${escapeText(evt.summary)}</div>` : args}`;
    feed.prepend(el);
    while (feed.children.length > 80) feed.lastChild.remove();
  }

  // ---------------- tasks ---------------- //
  upsertTask(task) {
    const feed = $("#tasks-feed");
    const empty = $(".empty", feed);
    if (empty) empty.remove();
    let el = this._tasks.get(task.id);
    if (!el) {
      el = document.createElement("div");
      el.className = "task";
      feed.prepend(el);
      this._tasks.set(task.id, el);
    }
    const done = ["completed", "failed", "cancelled"].includes(task.status);
    el.innerHTML = `
      <div class="task-head">
        <span class="task-title">${escapeText(task.title)}</span>
        <span class="status-tag ${task.status === "failed" ? "error" : task.status === "completed" ? "done" : "running"}">${task.status}</span>
      </div>
      <div class="task-bar"><i style="width:${Math.round((task.progress || 0) * 100)}%"></i></div>
      ${!done ? `<button class="task-cancel" data-cancel="${task.id}">Cancel</button>` : ""}`;
  }
  renderTasks(list) { (list || []).forEach((t) => this.upsertTask(t)); }

  // ---------------- memory ---------------- //
  renderMemory(list) {
    const feed = $("#memory-feed");
    feed.innerHTML = "";
    if (!list || !list.length) { feed.innerHTML = `<p class="empty">No memories yet.</p>`; return; }
    for (const m of list) {
      const el = document.createElement("div");
      el.className = "mem-item";
      el.innerHTML = `<div>
          <div class="mem-kind">${escapeText(m.kind)}${m.key ? " · " + escapeText(m.key) : ""}</div>
          ${escapeText(m.value)}
        </div>
        <button class="mem-del" data-mem="${m.id}" title="Forget">✕</button>`;
      feed.appendChild(el);
    }
  }

  // ---------------- audit ---------------- //
  renderAudit(list) {
    const feed = $("#audit-feed");
    feed.innerHTML = "";
    if (!list || !list.length) { feed.innerHTML = `<p class="empty">No audit entries.</p>`; return; }
    for (const a of list) {
      const el = document.createElement("div");
      el.className = `event ${a.level === "warning" ? "running" : a.level === "error" ? "error" : ""}`;
      el.innerHTML = `<div class="ev-head">
          <span class="ev-tool">${escapeText(a.event)}</span>
          <span class="ev-time">${fmtTime(a.created_at)}</span>
        </div>`;
      feed.appendChild(el);
    }
  }

  // ---------------- skills ---------------- //
  renderSkills(list) {
    const feed = $("#skills-feed");
    feed.innerHTML = "";
    if (!list || !list.length) {
      feed.innerHTML = `<p class="empty">No skills installed. Drop one in <code>skills/</code>.</p>`;
      return;
    }
    for (const s of list) {
      const statusClass = s.status === "loaded" ? "done"
                        : s.status === "error" ? "error" : "running";
      const tools = (s.tools || []).map((t) =>
        `<span class="skill-tool">${escapeText(t)}</span>`).join("");
      const el = document.createElement("div");
      el.className = `skill-item ${statusClass}`;
      el.innerHTML = `
        <div class="ev-head">
          <span class="ev-tool">${escapeText(s.name)}<small class="skill-ver">v${escapeText(s.version || "0.0.0")}</small></span>
          <span class="status-tag ${statusClass}">${escapeText(s.status)}</span>
        </div>
        ${s.description ? `<div class="ev-sum">${escapeText(s.description)}</div>` : ""}
        ${s.author ? `<div class="skill-author">by ${escapeText(s.author)}</div>` : ""}
        ${s.error ? `<div class="skill-error">${escapeText(s.error)}</div>` : ""}
        ${tools ? `<div class="skill-tools">${tools}</div>` : ""}`;
      feed.appendChild(el);
    }
  }

  // ---------------- toasts ---------------- //
  toast(message, level = "info") {
    const stack = $("#toast-stack");
    const t = document.createElement("div");
    t.className = `toast ${level}`;
    t.textContent = message;
    stack.appendChild(t);
    setTimeout(() => { t.classList.add("fade-out"); setTimeout(() => t.remove(), 400); }, 5200);
  }

  // ---------------- approval modal ---------------- //
  showApproval(p) {
    $("#appr-risk").textContent = p.risk || "medium";
    $("#appr-risk").className = "risk-pill risk-" + (p.risk || "medium");
    $("#appr-reason").textContent = p.reason || "This action needs your confirmation.";
    $("#appr-tool").textContent = p.tool || "";
    $("#appr-cat").textContent = p.category || "general";
    $("#appr-args").textContent = JSON.stringify(p.arguments || {}, null, 2);
    $("#appr-remember-row").style.display = p.rememberable ? "flex" : "none";
    $("#appr-remember").checked = false;
    $("#approval").hidden = false;
  }
  hideApproval() { $("#approval").hidden = true; }

  // ---------------- settings drawer ---------------- //
  openSettings() { $("#settings").hidden = false; }
  closeSettings() { $("#settings").hidden = true; }
  populateSettings(s) {
    $("#set-provider").value = s.ai_provider || "local";
    $("#set-permission").value = s.permission_mode || "balanced";
    $("#set-shell").checked = !!s.enable_shell;
    $("#set-tts").checked = s.tts_enabled !== false;
    $("#set-wake").value = s.wake_word || "hey jarvis";
    $("#set-memory").checked = s.memory_enabled !== false;
    $("#set-proactive").checked = s.proactive_enabled !== false;
    const rate = s.tts_rate || 175;
    $("#set-rate").value = rate;
    $("#rate-out").textContent = rate;
    // provider key hint (honest about what's configured — never shows the key)
    const hint = $("#key-hint");
    const prov = $("#set-provider").value;
    if (prov === "anthropic") hint.textContent = s.has_anthropic_key
      ? "Anthropic key detected in environment." : "No ANTHROPIC_API_KEY set — will fall back to Local.";
    else if (prov === "openai") hint.textContent = s.has_openai_key
      ? `OpenAI-compatible key detected (${s.openai_base_url}).` : "No OPENAI_API_KEY set — will fall back to Local.";
    else hint.textContent = "Local mode runs fully offline. No API key required.";
    // roots
    const roots = $("#set-roots");
    roots.innerHTML = "";
    (s.file_roots || []).forEach((r) => {
      const li = document.createElement("li"); li.textContent = r; roots.appendChild(li);
    });
  }
  flashSaved() {
    const tag = $("#settings-saved");
    tag.hidden = false;
    setTimeout(() => { tag.hidden = true; }, 1800);
  }
}

function escapeText(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function shortArgs(args) {
  const s = JSON.stringify(args);
  return s.length > 60 ? s.slice(0, 60) + "…" : s;
}
