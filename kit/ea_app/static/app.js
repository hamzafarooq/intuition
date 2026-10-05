/* Intuition front end. Vanilla JS, no build step, no network beyond this app's own API. */
(() => {
  "use strict";

  // ------------------------------------------------------------------ helpers

  const $ = (sel, root = document) => root.querySelector(sel);
  const PHONE = document.body.dataset.page === "phone"; // /phone/<session>: the phone on its own
  const chan = "BroadcastChannel" in window ? new BroadcastChannel("intuition-phone") : null;
  const ICONS = "/static/icons.svg";
  const icon = (name, cls = "icon") => `<svg class="${cls}" aria-hidden="true"><use href="${ICONS}#i-${name}"/></svg>`;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch { /* storage blocked */ } },
  };
  const clip = (s, n) => { s = String(s ?? ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
  const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; };

  async function api(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const r = await fetch(path, opts);
    let data = null;
    if (r.status !== 204) { try { data = await r.json(); } catch { data = null; } }
    if (!r.ok) { const err = new Error((data && data.error) || `Request failed (${r.status})`); err.status = r.status; throw err; }
    return data;
  }

  let toastTimer = 0;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 4200);
  }

  // ------------------------------------------------------------------ state

  const S = {
    cfg: null, sid: null, info: null, es: null, tz: "America/Denver",
    lastI: -1, replaying: true, online: false, busy: false, queued: 0,
    events: [], turns: new Map(), messages: new Map(), approvals: new Map(),
    worldBase: null, worldBaseAt: 0, world: null, worldTimer: 0, worldTabSeen: true,
    filter: "all", counts: {}, browserTimer: 0, speak: false, voiceOK: false, rec: null,
    openMail: new Set(), inboxAll: false, view: "landing",
  };

  const CAT = {
    tool_call: "tools", tool_result: "tools", skill_loaded: "skills", delegate: "agents", handoff_result: "agents",
    approval_request: "approvals", approval_response: "approvals", check: "checks", stop_check_block: "checks",
    signal: "signals", browser_step: "browser", site_event: "browser", user: "messages", assistant: "messages",
    thinking_summary: "messages", message_queued: "messages", status: "system", usage: "system", turn_end: "system",
    budget_exceeded: "system", error: "error", turn_start: "system", session_started: "system", reset: "system",
    option_changed: "system",
  };
  const TAG = {
    tool_call: "tool call", tool_result: "result", skill_loaded: "skill", delegate: "hand-off", handoff_result: "report",
    approval_request: "approval", approval_response: "decision", check: "check", stop_check_block: "stop check",
    signal: "signal", browser_step: "browser", site_event: "site", user: "Maya", assistant: "reply",
    thinking_summary: "reasoning", status: "status", usage: "usage", turn_end: "turn end", budget_exceeded: "budget",
    error: "error", message_queued: "sent", turn_start: "turn", session_started: "session", reset: "reset",
    option_changed: "option",
  };
  const FILTERS = [["all", "All"], ["tools", "Tools"], ["skills", "Skills"], ["agents", "Agents"], ["approvals", "Approvals"],
    ["checks", "Checks"], ["signals", "Signals"], ["browser", "Browser"], ["messages", "Messages"], ["system", "System"]];
  const CONN_ICON = { email: "mail", calendar: "calendar", contacts: "people", docs: "doc", web: "globe", travel: "plane" };
  const STATUS_LABEL = { done: "Done", partial: "Partly done", failed: "Failed", waiting: "Waiting on you", missing: "No status" };
  const STATUS_RE = /^[ \t]*\**STATUS:\**\s*(done|partial|failed|waiting)\b\s*(?:[—–-]\s*(.*))?$/gim;
  const groupOf = (e) => { const c = CAT[e.type] || "system"; return c === "error" ? "system" : c; };
  const catOf = (e) => (e.type === "tool_result" && e.ok === false) || (e.type === "check" && e.ok === false) ? "error" : (CAT[e.type] || "system");

  // ------------------------------------------------------------------ time

  function fmt(iso, opts) {
    const d = iso instanceof Date ? iso : new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    try { return new Intl.DateTimeFormat("en-GB", { hour12: false, timeZone: S.tz, ...opts }).format(d); }
    catch { return new Intl.DateTimeFormat("en-GB", { hour12: false, ...opts }).format(d); }
  }
  const hm = (iso) => fmt(iso, { hour: "2-digit", minute: "2-digit" });
  const hms = (iso) => fmt(iso, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  function worldNow() { return S.worldBase == null ? null : new Date(S.worldBase + (Date.now() - S.worldBaseAt)); }
  function tickClock() { const n = worldNow(); if (n) $("#sb-time").textContent = hm(n); }
  setInterval(tickClock, 10000);

  // ------------------------------------------------------------------ text

  function splitStatus(text) {
    let last = null;
    const body = String(text || "").replace(STATUS_RE, (m, s, r) => { last = { status: s.toLowerCase(), reason: (r || "").trim() }; return ""; }).trim();
    return { body, last };
  }
  function outputUrl(p) { return `/api/sessions/${encodeURIComponent(S.sid)}/outputs/${p.replace(/^\/?outputs\//, "").split("/").map(encodeURIComponent).join("/")}`; }
  function link(u, text) {
    const raw = u.replace(/&amp;/g, "&");
    if (/^https?:\/\//i.test(raw)) return `<a href="${u}" target="_blank" rel="noopener noreferrer">${text}</a>`;
    if (/^(outputs\/)?(briefs|decks|browser)\/[\w.\-]+$/.test(raw)) return `<a href="${esc(outputUrl(raw))}" target="_blank" rel="noopener">${text}</a>`;
    return text;
  }
  function inline(s) {
    let out = esc(s);
    out = out.replace(/`([^`]+)`/g, (m, c) => `<code>${c}</code>`);
    out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, t, u) => link(u, t));
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>");
    out = out.replace(/(^|[\s(])((?:outputs\/)?(?:briefs|decks|browser)\/[\w.\-]+\.(?:html|png|jpe?g|svg|webp))/g, (m, pre, p) => pre + link(p, p));
    return out;
  }
  function md(text) {
    let html = "", list = null, para = [];
    const flushP = () => { if (para.length) { html += `<p>${para.map(inline).join("<br>")}</p>`; para = []; } };
    const flushL = () => { if (list) { html += `</${list}>`; list = null; } };
    for (const raw of String(text || "").replace(/\r/g, "").split("\n")) {
      const line = raw.replace(/\s+$/, "");
      let m;
      if (!line.trim()) { flushP(); flushL(); continue; }
      if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) { flushP(); if (list !== "ul") { flushL(); html += "<ul>"; list = "ul"; } html += `<li>${inline(m[1])}</li>`; continue; }
      if ((m = line.match(/^\s*(\d+)[.)]\s+(.*)$/))) { flushP(); if (list !== "ol") { flushL(); html += `<ol start="${m[1]}">`; list = "ol"; } html += `<li>${inline(m[2])}</li>`; continue; }
      if ((m = line.match(/^#{1,6}\s+(.*)$/))) { flushP(); flushL(); html += `<h4>${inline(m[1])}</h4>`; continue; }
      flushL(); para.push(line);
    }
    flushP(); flushL();
    return html || "<p></p>";
  }
  const plain = (s) => String(s || "").replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").replace(/[*_`#>]/g, "").replace(/\s+/g, " ").trim();

  function argSummary(input) {
    if (!input || typeof input !== "object") return "";
    const parts = [];
    for (const [k, v] of Object.entries(input)) {
      if (v == null || v === "" || k === "idempotency_key") continue;
      let s;
      if (Array.isArray(v)) s = v.map((x) => (typeof x === "object" ? "{…}" : String(x))).join(", ");
      else if (typeof v === "object") s = "{…}";
      else if (typeof v === "boolean") s = v ? k : `no ${k}`;
      else s = String(v);
      parts.push(typeof v === "boolean" ? s : `${k} ${clip(s, 46)}`);
    }
    return clip(parts.join(" · "), 140);
  }
  function outputSummary(tool, out) {
    if (out == null) return "";
    if (typeof out === "string") {
      const t = out.trim();
      if (t.startsWith("{") || t.startsWith("[")) {
        try { return outputSummary(tool, JSON.parse(t)); } catch { /* truncated JSON */ }
        const n = (t.match(/"id":/g) || []).length;
        return `long result${n ? `, ${n}+ items` : ""} (${t.length.toLocaleString()} characters, cut short)`;
      }
      return clip(t, 120);
    }
    if (Array.isArray(out.results)) return `${out.results.length} result${out.results.length === 1 ? "" : "s"}${out.total != null ? ` of ${out.total}` : ""}`;
    if (Array.isArray(out.events)) return `${out.events.length} events`;
    if (Array.isArray(out.slots)) return out.slots.length ? `${out.slots.length} slot${out.slots.length === 1 ? "" : "s"}, first ${hm(out.slots[0].start)}` : "no slots";
    if (Array.isArray(out.matches)) return `${out.matches.length} match${out.matches.length === 1 ? "" : "es"}`;
    if (Array.isArray(out.options)) return `${out.options.length} options`;
    if (out.event_id) return `event ${out.event_id}${out.rsvps ? " · " + Object.entries(out.rsvps).filter(([p]) => p !== "maya").map(([p, s]) => `${p.split(".")[0]} ${s}`).join(", ") : ""}`;
    if (out.booking_id) return `booking ${out.booking_id}${out.total_usd != null ? ` · $${out.total_usd}` : ""}`;
    if (out.brief_id) return `brief ${out.brief_id} · ${out.words} words`;
    if (out.deck_id) return `deck ${out.deck_id}`;
    if (out.draft_id) return `draft ${out.draft_id}`;
    if (out.message_id) return `sent ${out.message_id}`;
    if (out.subject) return clip(out.subject, 80);
    if (out.now) return `${out.weekday || ""} ${hm(out.now)}`.trim();
    if (out.title) return clip(out.title, 80);
    if ("ok" in out) return out.ok ? "ok" : `${(out.problems || []).length} problems`;
    return clip(Object.keys(out).join(", "), 80);
  }
  const byLabel = (by) => ({ human: "you", app: "your 'yes'", script: "the script" }[by] || by || "?");

  // ------------------------------------------------------------------ one-line event descriptions (trace)

  function describe(e) {
    const a = e.agent && e.agent !== "main" ? `<span class="agent-tag">${esc(e.agent)}</span>` : "";
    switch (e.type) {
      case "tool_call":
        if (e.tool === "web_search") return `${a}Searched “${esc(e.input?.query)}”`;
        if (e.tool === "web_fetch") return `${a}Opened <span class="sub">${esc(e.input?.url)}</span>`;
        return `${a}<b>${esc(e.tool)}</b> <span class="sub">${esc(argSummary(e.input))}</span>`;
      case "tool_result":
        return `${a}${e.ok === false ? '<span class="bad">✗</span>' : '<span class="ok">✓</span>'} <b>${esc(e.tool)}</b> <span class="sub">${esc(e.ok === false ? clip(e.error, 160) : outputSummary(e.tool, e.output))}</span>`;
      case "skill_loaded": return `Loaded skill <b>${esc(e.skill)}</b>`;
      case "delegate": return `Handed to <b>${esc(e.to)}</b>: <span class="sub">${esc(clip(e.task, 180))}</span>`;
      case "handoff_result": return `<b>${esc(e.to)}</b> reported back: <span class="sub">${esc(clip(e.text, 180))}</span>`;
      case "approval_request": return `${a}Asked to approve <b>${esc(e.tool)}</b>: <span class="sub">${esc(clip(e.summary, 180))}</span>`;
      case "approval_response": return `${e.decision === "allow" ? '<span class="ok">Approved</span>' : '<span class="bad">Denied</span>'} by ${esc(byLabel(e.by))} <span class="sub">${esc(e.reason || "")}</span>`;
      case "check": {
        const probs = (e.problems || []).map((p) => p.detail || p.code).join("; ");
        return `${a}${e.ok ? '<span class="ok">✓</span>' : '<span class="bad">✗</span>'} <b>${esc(e.verifier)}</b> ${esc(e.object_id || "")} <span class="sub">${esc(clip(probs, 200))}</span>`;
      }
      case "stop_check_block": return `Stop check blocked the reply: <span class="sub">${esc(e.reason)}</span>`;
      case "signal": return `<b>${esc(e.kind)}</b> ${esc(e.detail || "")}`;
      case "browser_step": return `${a}<b>${esc((e.action || e.tool || "step").replace(/_page$/, ""))}</b> <span class="sub">${esc(clip(e.url || "", 90))}${e.target ? " · " + esc(e.target) : ""}${e.value ? " · “" + esc(e.value) + "”" : ""}</span>`;
      case "site_event": return `${a}<b>${esc(e.kind || e.event || "event")}</b> <span class="sub">${esc(e.site || "")} ${esc(e.hold_id || "")}${e.total_usd != null ? " · $" + esc(e.total_usd) : ""} ${esc(clip(e.url || "", 60))}</span>`;
      case "user": return `${e.source && e.source !== "maya" ? `<span class="agent-tag">${esc(e.source)}</span>` : ""}${esc(clip(e.text, 240))}`;
      case "assistant": return `${a}${esc(clip(plain(splitStatus(e.text).body), 240))}`;
      case "thinking_summary": return `${a}<em>${esc(clip(e.text, 240))}</em>`;
      case "status": return `${chipHtml(e.status, e.reason)}`;
      case "usage": return usageLine(e);
      case "turn_end": return `Stopped by <b>${esc(e.stopped_by)}</b>`;
      case "budget_exceeded": return `Budget exceeded: <b>${esc(e.which)}</b>`;
      case "error": return `<b>${esc(e.where || "error")}</b> <span class="sub">${esc(clip(e.message, 240))}</span>`;
      case "message_queued": return `${esc(clip(e.text, 200))}${e.queued ? ' <span class="sub">(queued)</span>' : ""}`;
      case "turn_start": return `Turn ${esc(e.turn)} started`;
      case "session_started": return `Run <b>${esc(e.run_name)}</b> · ${esc(e.options?.harness)} · ${esc(e.options?.model)} · search ${esc(e.options?.search_mode)}${e.options?.browser ? " · browser" : ""}`;
      case "reset": return "The world and the conversation were reset";
      case "option_changed": return `Auto-approve after a yes is now <b>${e.auto_approve ? "on" : "off"}</b>`;
      default: return `<span class="sub">${esc(clip(JSON.stringify(e), 200))}</span>`;
    }
  }
  function usageLine(e) {
    const k = (n) => (n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n || 0));
    const cost = e.cost_usd != null ? `$${Number(e.cost_usd).toFixed(e.cost_usd < 0.1 ? 4 : 2)}` : "";
    return [cost, e.seconds != null ? `${Number(e.seconds).toFixed(1)} s` : "", `${k(e.input_tokens)} in`, `${k(e.output_tokens)} out`,
      e.cache_read_tokens ? `${k(e.cache_read_tokens)} cached` : "", e.model ? esc(e.model) : ""].filter(Boolean).join(" · ");
  }
  function chipHtml(status, reason) {
    const s = STATUS_LABEL[status] ? status : "missing";
    return `<span class="chip chip-${s}" title="${esc(reason || "")}"><i></i><span class="lab">${esc(STATUS_LABEL[s])}${reason ? `<span class="reason">: ${esc(clip(reason, 60))}</span>` : ""}</span></span>`;
  }

  // ------------------------------------------------------------------ live trace

  function traceInit() {
    const f = $("#filters");
    f.innerHTML = FILTERS.map(([id, label]) => `<button class="filter cat-${id === "all" ? "system" : id}" type="button" data-filter="${id}" aria-pressed="${id === S.filter}">${id === "all" ? "" : "<i></i>"}${label} <span class="n" data-n="${id}"></span></button>`).join("");
    f.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-filter]"); if (!b) return;
      S.filter = b.dataset.filter;
      for (const x of f.querySelectorAll("[data-filter]")) x.setAttribute("aria-pressed", String(x === b));
      applyFilter();
    });
    const panel = $("#panel-trace");
    panel.addEventListener("scroll", () => { if (nearBottom(panel)) $("#trace-jump").hidden = true; });
    $("#trace-jump").addEventListener("click", () => { panel.scrollTop = panel.scrollHeight; $("#trace-jump").hidden = true; });
    $("#trace-list").addEventListener("toggle", (ev) => {
      const d = ev.target; if (!(d instanceof HTMLDetailsElement) || !d.open || d.dataset.filled) return;
      const e = S.events.find((x) => String(x._i) === d.dataset.i);
      if (!e) return;
      d.dataset.filled = "1";
      const pre = el("pre", null); pre.textContent = JSON.stringify(e, null, 2); d.append(pre);
    }, true);
  }
  function applyFilter() {
    for (const row of $("#trace-list").children) row.hidden = !(S.filter === "all" || row.dataset.group === S.filter || row.classList.contains("turn-sep"));
  }
  function nearBottom(n) { return n.scrollHeight - n.scrollTop - n.clientHeight < 80; }
  function traceAppend(e) {
    if (e.type === "reset" || PHONE) return;
    const panel = $("#panel-trace");
    const stick = S.replaying || nearBottom(panel);
    const list = $("#trace-list");
    if (e.type === "turn_start") {
      const msg = S.messages.get(e.message_id);
      const sep = el("li", "ev turn-sep", `<summary><span class="turn-label">Turn ${esc(e.turn)}<small>${esc(clip(msg?.text || "", 80))}</small></span></summary>`);
      list.append(sep);
    }
    if (e.type === "turn_start" || (e.type === "message_queued" && !e.queued)) return; // the turn separator says it
    const g = groupOf(e);
    const row = el("details", `ev cat-${catOf(e)}`);
    row.dataset.group = g; row.dataset.i = String(e._i);
    const tag = e.type === "user" ? (e.source && e.source !== "maya" ? e.source : "Maya") : e.type === "assistant" && e.agent !== "main" ? "agent says" : TAG[e.type] || e.type;
    let extra = "";
    const shot = screenshotPath(e);
    if (shot) extra = `<img class="thumb" loading="lazy" alt="Screenshot of ${esc(e.url || "the page")}" src="${esc(outputUrl(shot))}">`;
    row.innerHTML = `<summary><span class="ev-time">${esc(hms(e.world_ts || e.ts))}</span><span class="ev-tag">${esc(tag)}</span><span class="ev-body">${describe(e)}${extra}</span></summary>`;
    row.hidden = !(S.filter === "all" || g === S.filter);
    list.append(row);
    S.counts[g] = (S.counts[g] || 0) + 1; S.counts.all = (S.counts.all || 0) + 1;
    const n = $(`[data-n="${g}"]`); if (n) n.textContent = S.counts[g];
    $(`[data-n="all"]`).textContent = S.counts.all;
    $("#trace-count").textContent = S.counts.all;
    $("#trace-empty").hidden = true;
    if (!S.replaying) { if (stick) panel.scrollTop = panel.scrollHeight; else $("#trace-jump").hidden = false; }
  }
  function screenshotPath(e) {
    const p = e.path || e.screenshot || (e.input && (e.input.filePath || e.input.path));
    if (typeof p === "string" && /(^|\/)outputs\/browser\/[^/]+$|^browser\/[^/]+$/.test(p)) return p.replace(/^.*?(outputs\/browser\/|browser\/)/, "browser/");
    return null;
  }
  function traceClear() {
    $("#trace-list").innerHTML = ""; S.counts = {};
    for (const n of document.querySelectorAll("[data-n]")) n.textContent = "";
    $("#trace-count").textContent = "0"; $("#trace-empty").hidden = false;
  }

  // ------------------------------------------------------------------ chat

  const log = () => $("#chat-log");
  function chatStick() { const b = $("#chat-body"); return S.replaying || nearBottom(b); }
  function chatAfter(stick) {
    const b = $("#chat-body");
    if (S.replaying) return;
    if (stick) b.scrollTop = b.scrollHeight; else $("#chat-jump").hidden = false;
  }
  function chatClear() {
    log().innerHTML = ""; $("#chat-outbox").innerHTML = "";
    S.turns.clear(); S.messages.clear(); S.approvals.clear();
    updateEmpty();
  }
  function updateEmpty() {
    const empty = S.messages.size === 0;
    $("#chat-empty").hidden = !empty;
    renderSuggestions(empty);
  }
  function renderSuggestions(empty) {
    const box = $("#suggestions");
    const open = $("#plus-btn").getAttribute("aria-expanded") === "true";
    const list = [...(S.cfg?.suggestions || []), ...(open ? S.cfg?.extra_suggestions || [] : [])];
    box.hidden = !(empty || open);
    box.classList.toggle("wrap", open || empty);
    box.innerHTML = list.map((s) => `<button class="suggestion" type="button">${esc(s)}</button>`).join("");
  }
  function textHtml(t) { return esc(t).replace(/\n/g, "<br>"); }

  function onMessageQueued(e) {
    const stick = chatStick();
    const row = el("div", "row me first");
    row.innerHTML = `<div class="bubble me tail queued"><div class="txt"><p>${textHtml(e.text)}</p></div><span class="meta"><span class="meta-label">sending…</span> ${icon("clock", "tick wait")}</span></div>`;
    (e.queued ? $("#chat-outbox") : log()).append(row);
    S.messages.set(e.message_id, { row, text: e.text, ts: e.world_ts });
    updateEmpty();
    chatAfter(stick);
  }
  function setTick(m, kind) {
    const meta = m.row.querySelector(".meta");
    const time = esc(hm(m.ts));
    m.row.querySelector(".bubble").classList.remove("queued");
    meta.innerHTML = kind === "read"
      ? `${time} <svg class="tick read" aria-label="Read"><use href="${ICONS}#i-read"/></svg>`
      : `${time} <svg class="tick sent" aria-label="Delivered"><use href="${ICONS}#i-sent"/></svg>`;
  }
  function onTurnStart(e) {
    const stick = chatStick();
    const m = S.messages.get(e.message_id);
    if (m) { log().append(m.row); m.ts = m.ts || e.world_ts; setTick(m, "sent"); }
    const group = el("div", "turn-group");
    const content = el("div", "turn-content");
    const typing = el("div", "turn-typing", `<div class="row them first"><div class="bubble them tail typing" aria-label="Intuition is typing"><i></i><i></i><i></i></div></div><div class="typing-label"></div>`);
    const under = el("div", "row them under");
    const hood = el("div", "hood"); hood.hidden = true;
    group.append(content, typing, under, hood);
    log().append(group);
    const t = { n: e.turn, mid: e.message_id, group, content, typing, under, hood, events: [], status: null, usage: null, done: false, open: false, replies: 0 };
    S.turns.set(e.turn, t);
    renderUnder(t);
    chatAfter(stick);
  }
  function turnFor(e) { return e.turn != null ? S.turns.get(e.turn) : null; }
  function addThemRow(t, node) {
    const last = t.content.lastElementChild;
    if (last && last.classList.contains("them") && !last.classList.contains("under")) last.querySelector(".bubble")?.classList.remove("tail");
    else node.classList.add("first");
    t.content.append(node);
  }
  function onAssistant(t, e) {
    const { body, last } = splitStatus(e.text);
    if (last && !t.status) t.parsedStatus = last;
    if (!body) return;
    const stick = chatStick();
    const row = el("div", "row them");
    row.innerHTML = `<div class="bubble them tail"><div class="txt">${md(body)}</div><span class="meta">${esc(hm(e.world_ts || e.ts))}</span></div>`;
    addThemRow(t, row);
    t.replies++;
    if (S.speak && !S.replaying && "speechSynthesis" in window) {
      try { speechSynthesis.speak(new SpeechSynthesisUtterance(plain(body))); } catch { /* ignore */ }
    }
    chatAfter(stick);
  }
  function cardRows(e) {
    const c = e.card || {};
    const rows = (c.rows || []).filter((r) => r && r[1]).map(([k, v]) => `<div><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("");
    return { title: c.title || `Allow ${e.tool}?`, rows, body: c.body };
  }
  function onApprovalRequest(t, e) {
    const stick = chatStick();
    const { title, rows, body } = cardRows(e);
    const row = el("div", "row them");
    row.innerHTML = `<div class="approval" data-call="${esc(e.call_id)}">
      <div class="ap-head">${icon("shield")} Needs your OK <span class="ap-tool">${esc(e.tool)}</span></div>
      <div class="ap-title">${esc(title)}</div>
      ${rows ? `<div class="ap-rows">${rows}</div>` : `<div class="ap-summary">${esc(e.summary || "")}</div>`}
      ${body ? `<div class="ap-body">${esc(body)}</div>` : ""}
      <div class="ap-actions"><button class="btn btn-ghost btn-s btn-pill" type="button" data-decide="deny">Deny</button><button class="btn btn-primary btn-s btn-pill" type="button" data-decide="allow">Approve</button></div>
    </div>`;
    addThemRow(t, row);
    S.approvals.set(String(e.call_id), row.querySelector(".approval"));
    chatAfter(stick);
  }
  function onApprovalResponse(e) {
    const card = S.approvals.get(String(e.call_id));
    if (!card) return;
    card.classList.add("decided");
    const allow = e.decision === "allow";
    const label = allow ? (e.by === "app" ? "Approved by your 'yes'" : e.by === "script" ? "Approved by the script" : "Approved") : (e.by === "human" ? "Denied" : `Denied by ${byLabel(e.by)}`);
    const reason = !allow && e.by !== "human" && e.reason ? `<div class="ap-wait">${esc(e.reason)}</div>` : "";
    const actions = card.querySelector(".ap-actions, .ap-wait");
    const res = el("div", `ap-result ${allow ? "allow" : "deny"}`, `${icon(allow ? "check" : "x")} ${esc(label)}`);
    card.querySelectorAll(".ap-actions, .ap-wait, .ap-result").forEach((n) => n.remove());
    card.append(res);
    if (reason) card.insertAdjacentHTML("beforeend", reason);
    void actions;
  }
  async function decide(card, decision) {
    const id = card.dataset.call;
    card.querySelectorAll("[data-decide]").forEach((b) => { b.disabled = true; });
    try {
      await api("POST", `/api/sessions/${S.sid}/approvals/${encodeURIComponent(id)}`, { decision });
      if (!card.classList.contains("decided")) {
        card.querySelector(".ap-actions")?.replaceWith(el("div", "ap-wait", decision === "allow" ? "Approved. Passing it on…" : "Denied. Telling the assistant…"));
      }
    } catch (err) {
      card.querySelectorAll("[data-decide]").forEach((b) => { b.disabled = false; });
      toast(err.message);
    }
  }
  function onSystemNote(e) {
    const stick = chatStick();
    const n = el("div", "day-chip", esc(e.detail || e.message || ""));
    log().append(n);
    chatAfter(stick);
  }
  const VERB = {
    email_search: "Searching your email", email_read: "Reading an email", email_draft: "Drafting an email",
    email_update_draft: "Editing the draft", email_send: "Sending the email", calendar_list: "Checking your calendar",
    calendar_get: "Reading an event", calendar_find_free: "Looking for free time", calendar_create: "Creating the event",
    calendar_update: "Changing the event", calendar_cancel: "Cancelling the event", contacts_lookup: "Looking someone up",
    docs_search: "Searching documents", docs_read: "Reading a document", web_search: "Searching the web",
    web_fetch: "Opening a page", travel_search: "Searching flights and hotels", travel_book: "Booking",
    restaurant_search: "Finding a table", restaurant_book: "Booking a table", brief_save: "Saving the brief",
    deck_create: "Building the deck", clock_now: "Checking the time",
  };
  function activity(e) {
    const who = e.agent && e.agent !== "main" ? `${e.agent}: ` : "";
    switch (e.type) {
      case "skill_loaded": return `Reading the ${e.skill} playbook`;
      case "delegate": return `Asking the ${e.to} specialist`;
      case "handoff_result": return `${e.to} reported back`;
      case "tool_call": return who + (e.tool?.startsWith("check_") ? "Double-checking" : VERB[e.tool] || `Using ${e.tool}`);
      case "approval_request": return "Waiting for your OK";
      case "browser_step": return who + "Working in the browser";
      case "thinking_summary": return who + "Thinking";
      default: return null;
    }
  }
  function renderUnder(t) {
    const steps = buildSteps(t.events).length;
    const status = t.status || t.parsedStatus;
    const chip = t.done ? chipHtml(status ? status.status : "missing", status ? status.reason : "") : "";
    const u = t.usage;
    const bits = [`${steps} step${steps === 1 ? "" : "s"}${t.done ? "" : " so far"}`];
    if (t.done && u && u.seconds != null) bits.push(`${Number(u.seconds).toFixed(1)} s`);
    t.under.innerHTML = `${chip}<button class="hood-toggle" type="button" aria-expanded="${t.open}" data-turn="${t.n}">Under the hood · ${esc(bits.join(" · "))} ${icon("chevron")}</button>`;
    t.under.hidden = !t.done && steps === 0;
  }
  function onTurnEvent(t, e) {
    t.events.push(e);
    const act = activity(e);
    if (act) t.typing.querySelector(".typing-label").textContent = act + "…";
    if (e.type === "status") t.status = { status: e.status, reason: e.reason };
    if (e.type === "usage") t.usage = e;
    if (e.type === "user" && (!e.source || e.source === "maya")) { const m = S.messages.get(t.mid); if (m) setTick(m, "read"); }
    if (e.type === "assistant" && (!e.agent || e.agent === "main")) onAssistant(t, e);
    if (e.type === "approval_request") onApprovalRequest(t, e);
    if (e.type === "approval_response") onApprovalResponse(e);
    if (e.type === "turn_end") {
      t.done = true; t.typing.hidden = true;
      const m = S.messages.get(t.mid); if (m) setTick(m, "read");
      if (!t.replies) {
        const err = t.events.filter((x) => x.type === "error").map((x) => x.message).join(" ");
        const row = el("div", "row them");
        row.innerHTML = `<div class="bubble them tail note"><div class="txt"><p>${esc(err || `No reply (stopped by ${e.stopped_by}).`)}</p></div><span class="meta">${esc(hm(e.world_ts || e.ts))}</span></div>`;
        addThemRow(t, row);
      }
    }
    renderUnder(t);
    if (t.open) renderHood(t);
  }

  // ------------------------------------------------------------------ under the hood (per turn)

  function buildSteps(events) {
    const results = new Map(), responses = new Map(), calls = new Set(), requests = new Set();
    for (const e of events) {
      if (e.type === "tool_result") results.set(e.call_id, e);
      if (e.type === "approval_response") responses.set(String(e.call_id), e);
      if (e.type === "tool_call") calls.add(e.call_id);
      if (e.type === "approval_request") requests.add(String(e.call_id));
    }
    const handoffs = new Set(events.filter((e) => e.type === "handoff_result").map((e) => (e.text || "").trim()));
    const steps = [];
    for (const e of events) {
      switch (e.type) {
        case "tool_call": steps.push({ e, res: results.get(e.call_id) }); break;
        case "tool_result": if (!calls.has(e.call_id)) steps.push({ e }); break;
        case "approval_request": steps.push({ e, res: responses.get(String(e.call_id)) }); break;
        case "approval_response": if (!requests.has(String(e.call_id))) steps.push({ e }); break;
        case "assistant": if (e.agent && e.agent !== "main" && !handoffs.has((e.text || "").trim())) steps.push({ e }); break;
        case "user": if (e.source && e.source !== "maya") steps.push({ e }); break;
        case "turn_end": if (e.stopped_by && e.stopped_by !== "end_turn") steps.push({ e }); break;
        case "status": case "usage": case "message_queued": case "turn_start": case "session_started": case "option_changed": break;
        default: steps.push({ e });
      }
    }
    return steps;
  }
  const STEP_ICON = {
    tool_call: "tool", tool_result: "tool", skill_loaded: "skill", delegate: "agent", handoff_result: "return",
    approval_request: "shield", approval_response: "shield", check: "check", stop_check_block: "stop", signal: "bell",
    browser_step: "browser", site_event: "browser", thinking_summary: "thought", assistant: "agent", user: "alert",
    error: "alert", budget_exceeded: "coin", turn_end: "stop",
  };
  function stepHtml(s, idx) {
    const e = s.e, r = s.res;
    let ic = STEP_ICON[e.type] || "dots", main = "", sub = "", extra = "";
    const who = e.agent && e.agent !== "main" ? `<span class="agent-tag">${esc(e.agent)}</span>` : "";
    switch (e.type) {
      case "tool_call": {
        const mark = r ? (r.ok === false ? ' <span class="bad">✗</span>' : ' <span class="ok">✓</span>') : " <span class=\"sub\">…</span>";
        if (e.tool === "web_search") {
          ic = "search"; main = `${who}Searched “${esc(e.input?.query)}”${mark}`;
          const res = r?.output?.results; sub = Array.isArray(res) ? (res.length ? res.slice(0, 3).map((x) => x.title).join(" · ") : "No results") : esc(r?.error || "");
          sub = esc(sub);
        } else if (e.tool === "web_fetch") {
          ic = "page"; main = `${who}Opened a page${mark}`; sub = esc(clip(e.input?.url, 90)) + (r?.output?.title ? " · " + esc(r.output.title) : r?.error ? " · " + esc(r.error) : "");
        } else {
          main = `${who}<b>${esc(e.tool)}</b>${mark}`;
          sub = esc(argSummary(e.input)) + (r ? ` → ${esc(r.ok === false ? clip(r.error, 140) : outputSummary(e.tool, r.output))}` : "");
        }
        break;
      }
      case "tool_result": main = `${who}<b>${esc(e.tool)}</b> ${e.ok === false ? '<span class="bad">✗</span>' : '<span class="ok">✓</span>'}`; sub = esc(e.ok === false ? e.error : outputSummary(e.tool, e.output)); break;
      case "skill_loaded": main = `Loaded skill <b>${esc(e.skill)}</b>`; break;
      case "delegate": main = `Handed off to <b>${esc(e.to)}</b>`; sub = esc(e.task); break;
      case "handoff_result": main = `<b>${esc(e.to)}</b> reported back`; sub = esc(e.text); break;
      case "approval_request": {
        const dec = r ? (r.decision === "allow" ? `<span class="ok">approved by ${esc(byLabel(r.by))}</span>` : `<span class="bad">denied by ${esc(byLabel(r.by))}</span>`) : "<span class=\"sub\">waiting</span>";
        main = `Asked to approve <b>${esc(e.tool)}</b> · ${dec}`; sub = esc(e.summary); break;
      }
      case "approval_response": main = `${e.decision === "allow" ? "Approved" : "Denied"} by ${esc(byLabel(e.by))}`; sub = esc(e.reason); break;
      case "check": {
        ic = e.ok ? "check" : "x";
        main = `${who}${e.ok ? '<span class="ok">✓</span>' : '<span class="bad">✗</span>'} <b>${esc(e.verifier)}</b> ${esc(e.object_id || "")}`;
        if (!e.ok && (e.problems || []).length) extra = `<ul class="problems">${e.problems.map((p) => `<li>${esc(p.detail || p.code)}</li>`).join("")}</ul>`;
        break;
      }
      case "stop_check_block": main = "Stop check sent it back"; sub = esc(e.reason); break;
      case "signal": main = `<b>${esc(signalLabel(e.kind))}</b>`; sub = esc(e.detail); break;
      case "browser_step": {
        main = `${who}Browser: <b>${esc((e.action || e.tool || "step").replace(/_page$/, "").replace(/_/g, " "))}</b>`;
        sub = esc(clip(e.url || "", 90)) + (e.target ? " · " + esc(e.target) : "") + (e.value ? " · “" + esc(e.value) + "”" : "");
        const shot = screenshotPath(e);
        if (shot) extra = `<img class="thumb" loading="lazy" alt="Screenshot of ${esc(e.url || "the page")}" src="${esc(outputUrl(shot))}">`;
        break;
      }
      case "site_event": main = `${who}Site: <b>${esc((e.kind || e.event || "event").replace(/_/g, " "))}</b> ${esc(e.hold_id || "")}${e.total_usd != null ? " · $" + esc(e.total_usd) : ""}`; sub = esc(clip(e.url || "", 90)); break;
      case "thinking_summary": main = `${who}<em>${esc(e.text)}</em>`; break;
      case "assistant": main = `${who}said`; sub = esc(splitStatus(e.text).body); break;
      case "user": main = `Note from the ${esc(e.source)}`; sub = esc(e.text); break;
      case "error": main = `<span class="bad">Error</span> in ${esc(e.where)}`; sub = esc(e.message); break;
      case "budget_exceeded": main = `Budget exceeded: <b>${esc(e.which)}</b>`; break;
      case "turn_end": main = `Stopped by <b>${esc(e.stopped_by)}</b>`; break;
      default: main = esc(e.type);
    }
    const cat = catOf(r && r.ok === false ? r : e);
    const nested = e.agent && e.agent !== "main" ? " nested" : "";
    return `<li class="step cat-${cat}${nested}"><button class="step-line" type="button" data-step="${idx}" aria-expanded="false">${icon(ic)}<span class="s-main">${main}${sub ? `<span class="s-sub">${sub}</span>` : ""}${extra}</span></button></li>`;
  }
  function signalLabel(kind) {
    return { decline: "Declined", reply: "Reply", travel_flag: "Travel desk flag", disconnect: "Disconnected", edit: "Edited", deny: "Denied", correction: "Correction" }[kind] || kind;
  }
  function renderHood(t) {
    const steps = buildSteps(t.events);
    t.steps = steps;
    const u = t.usage;
    const counts = { tools: 0, agents: 0, checks: 0 };
    for (const s of steps) { if (s.e.type === "tool_call") counts.tools++; if (s.e.type === "delegate") counts.agents++; if (s.e.type === "check") counts.checks++; }
    t.hood.innerHTML = `<ol>${steps.map(stepHtml).join("") || '<li class="step"><span class="s-sub">No steps yet.</span></li>'}</ol>
      <div class="hood-foot"><span><b>${counts.tools}</b> tool calls</span><span><b>${counts.agents}</b> hand-offs</span><span><b>${counts.checks}</b> checks</span>${u ? `<span>${usageLine(u)}</span>` : t.done ? "" : "<span>running…</span>"}</div>`;
  }
  function toggleHood(n) {
    const t = S.turns.get(Number(n)); if (!t) return;
    t.open = !t.open; t.hood.hidden = !t.open;
    if (t.open) renderHood(t);
    renderUnder(t);
  }
  function toggleStep(t, btn) {
    const li = btn.parentElement;
    const open = btn.getAttribute("aria-expanded") === "true";
    btn.setAttribute("aria-expanded", String(!open));
    const existing = li.querySelector("pre");
    if (open) { existing?.remove(); return; }
    const s = t.steps?.[Number(btn.dataset.step)]; if (!s) return;
    const e = s.e;
    let data = e;
    if (e.type === "tool_call") data = { tool: e.tool, agent: e.agent, input: e.input, ok: s.res?.ok, output: s.res?.output, error: s.res?.error || undefined };
    else if (e.type === "approval_request") data = { tool: e.tool, input: e.input, summary: e.summary, decision: s.res };
    const pre = el("pre"); pre.textContent = JSON.stringify(data, null, 2); li.append(pre);
  }

  // ------------------------------------------------------------------ event dispatch

  function handle(e) {
    if (e._i !== undefined) { if (e._i <= S.lastI) return; S.lastI = e._i; }
    switch (e.type) {
      case "session_state": onState(e); if (S.replaying) { S.replaying = false; afterReplay(); } return;
      case "world_changed": scheduleWorld(); return;
      case "reset": resetUI(); return;
      case "session_started": onSessionStarted(e); break;
      default: break;
    }
    S.events.push(e);
    traceAppend(e);
    if (e.type === "message_queued") onMessageQueued(e);
    else if (e.type === "turn_start") onTurnStart(e);
    else if (e.type === "signal" && e.source === "app" && e.kind === "disconnect") { onSystemNote(e); const t = turnFor(e); if (t && !t.done) onTurnEvent(t, e); }
    else {
      const t = turnFor(e);
      if (t) onTurnEvent(t, e);
    }
    if (e.type === "browser_step" || e.type === "site_event") browserBanner(true);
    if (e.type === "turn_end") browserBanner(false);
  }
  function onState(e) {
    S.busy = !!e.busy; S.queued = e.queued || 0;
    if (e.world_now) { S.worldBase = Date.parse(e.world_now); S.worldBaseAt = Date.now(); tickClock(); }
    renderPresence();
  }
  function renderPresence() {
    const sub = $("#chat-sub");
    let text = "online";
    if (!S.online) text = "connecting…";
    else if (S.busy) text = S.queued ? `typing… · ${S.queued} waiting` : "typing…";
    sub.textContent = text;
    sub.classList.toggle("working", S.online && S.busy);
    $("#presence").classList.toggle("off", !S.online);
  }
  function onSessionStarted(e) {
    if (e.timezone) S.tz = e.timezone;
    if (e.world_start) { S.worldBase = Date.parse(e.world_start); S.worldBaseAt = Date.now(); tickClock(); }
    $("#run-pill").textContent = e.run_name || "";
    $("#run-pill").title = e.run_dir || "";
    const on = Object.entries(e.options?.connectors || {}).filter(([, v]) => v).map(([k]) => (S.cfg?.connectors || []).find((c) => c.id === k)?.label.toLowerCase() || k);
    $("#hello-connected").textContent = on.length ? listText(on) : "nothing yet: no accounts are connected";
    $("#menu-auto").checked = !!e.options?.auto_approve;
  }
  function listText(a) { return a.length <= 1 ? a.join("") : a.slice(0, -1).join(", ") + " and " + a[a.length - 1]; }
  function afterReplay() {
    const b = $("#chat-body"); b.scrollTop = b.scrollHeight;
    const p = $("#panel-trace"); p.scrollTop = p.scrollHeight;
    for (const t of S.turns.values()) { if (!t.done) t.typing.hidden = false; }
    updateEmpty();
    loadWorld(true);
  }
  function resetUI() {
    S.events = []; chatClear(); traceClear(); browserBanner(false);
    S.world = null; S.openMail.clear();
    if (!S.replaying) toast("Fresh world, fresh conversation.");
  }
  function browserBanner(on) {
    clearTimeout(S.browserTimer);
    const show = on && !S.replaying;
    $("#phone-browser-banner").hidden = !show; $("#bts-browser-banner").hidden = !show;
    if (show) S.browserTimer = setTimeout(() => browserBanner(false), 9000);
  }

  // ------------------------------------------------------------------ SSE

  function connect() {
    if (S.es) S.es.close();
    S.replaying = true; S.lastI = -1;
    const es = new EventSource(`/api/sessions/${encodeURIComponent(S.sid)}/events`);
    S.es = es;
    es.onopen = () => { S.online = true; renderPresence(); };
    es.onmessage = (m) => { let e; try { e = JSON.parse(m.data); } catch { return; } handle(e); };
    es.onerror = async () => {
      S.online = false; renderPresence();
      try {
        const r = await fetch(`/api/sessions/${encodeURIComponent(S.sid)}`);
        if (r.status === 404) { es.close(); sessionGone(); }
      } catch { /* server down; EventSource keeps retrying */ }
    };
  }
  function sessionGone() {
    if (PHONE) {
      S.online = false; renderPresence();
      log().append(el("div", "day-chip", "This conversation has ended. Start a new one from the main window."));
      return;
    }
    store.set("intuition:session", null);
    S.sid = null;
    toast("That session has ended (the app restarted). Start a new one.");
    showView("landing");
    $("#setup").scrollIntoView();
  }

  // ------------------------------------------------------------------ Maya's world

  function scheduleWorld() {
    clearTimeout(S.worldTimer);
    S.worldTimer = setTimeout(() => loadWorld(false), 250);
  }
  async function loadWorld(quiet) {
    if (!S.sid || PHONE) return;
    try { S.world = await api("GET", `/api/sessions/${S.sid}/world`); }
    catch { return; }
    renderWorld();
    if (!quiet && $("#tab-world").getAttribute("aria-selected") !== "true" && !S.replaying) $("#world-dot").hidden = false;
  }
  function nameOf(id) { return S.world?.contacts?.[id]?.name || id; }
  function firstName(id) { return nameOf(id).split(" ")[0]; }
  function rsvpBadges(ev) {
    const out = [];
    for (const a of ev.attendees || []) {
      if (a === "maya") continue;
      const s = (ev.rsvps || {})[a] || "needs_action";
      const sym = { accepted: "✓", declined: "✗", tentative: "?", needs_action: "…" }[s] || "";
      const reason = (ev.rsvp_reasons || {})[a];
      out.push(`<span class="rsvp ${esc(s)}" title="${esc(nameOf(a))}: ${esc(s.replace("_", " "))}${reason ? " · " + esc(reason) : ""}">${esc(firstName(a))} ${sym}</span>`);
    }
    return out.length ? `<div class="rsvps">${out.join("")}</div>` : "";
  }
  function calEvent(ev) {
    const cls = ["cal-ev"]; if (ev.created_in_run) cls.push("new"); if (ev.status === "cancelled") cls.push("cancelled");
    return `<div class="${cls.join(" ")}"><span class="cal-time">${esc(ev.time_label)}</span><div><div class="cal-title">${esc(ev.title)}${ev.private ? ' <span class="sub">(private)</span>' : ""}</div>${rsvpBadges(ev)}</div></div>`;
  }
  function renderWorld() {
    const w = S.world; if (!w) return;
    const box = $("#world");
    const conns = Object.entries(w.connectors || {});
    const connHtml = conns.map(([k, v]) => {
      const on = v !== "disconnected";
      const label = (S.cfg.connectors.find((c) => c.id === k) || {}).label || k;
      return `<div class="conn-tile ${on ? "" : "off"}">${icon(CONN_ICON[k] || "plug")}<span class="c-text"><span class="c-name">${esc(label)}</span><span class="c-state">${on ? "Connected" : "Disconnected"}</span></span>
        <label class="switch s"><input type="checkbox" data-conn="${esc(k)}" ${on ? "checked" : "disabled"} aria-label="${esc(label)} connector"><span class="track"></span></label></div>`;
    }).join("");
    const days = (w.week?.days || []).map((d) => `<div class="day"><div class="day-name">${esc(d.label)}${d.today ? '<span class="today">Today</span>' : ""}</div>${d.events.length ? d.events.map(calEvent).join("") : '<p class="w-empty">Nothing booked.</p>'}</div>`).join("");
    const later = (w.week?.later || []).length ? `<div class="day"><div class="day-name">Coming up</div>${w.week.later.map((ev) => calEvent({ ...ev, time_label: ev.local_date ? fmt(ev.start, { weekday: "short", day: "numeric", month: "short" }) : ev.time_label })).join("")}</div>` : "";
    const inbox = w.inbox || [];
    const newIds = new Set(w.new_arrivals || []);
    const shown = S.inboxAll ? inbox : inbox.slice(0, 8);
    const mail = (m, folder) => {
      const open = S.openMail.has(m.id) ? " open" : "";
      const who = folder === "inbox" ? (m.from_name || m.from) : `To ${(m.to || []).join(", ")}`;
      const when = m.received_at || m.sent_at || m.updated_at;
      const cls = ["mail"]; if (m.unread) cls.push("unread"); if (newIds.has(m.id)) cls.push("new");
      return `<details class="${cls.join(" ")}" data-mail="${esc(m.id)}"${open}><summary><span class="dot"></span><span><span class="m-from">${esc(who)}</span><span class="m-subj">${esc(m.subject)}</span></span><span class="m-time">${esc(fmt(when, { weekday: "short", hour: "2-digit", minute: "2-digit" }))}</span></summary><div class="m-body">${esc(m.body || "")}</div></details>`;
    };
    const outbox = w.outbox || [];
    const drafts = (w.drafts || []).filter((d) => d.status === "draft");
    const bookings = w.bookings || [];
    const holds = w.holds || [];
    const links = w.links || { briefs: [], decks: [], browser: [] };
    const docLinks = [...links.briefs.map((l) => ({ ...l, ic: "doc" })), ...links.decks.map((l) => ({ ...l, ic: "slides" }))];
    box.innerHTML = `
      <section class="w-card wide"><div class="w-head">${icon("plug")}<h3>Connectors</h3></div>
        <div class="conn-grid">${connHtml}</div>
        <div class="w-note"><span>Turning one off disconnects it in the tool server for the rest of this session. It can't be turned back on.</span>
        <button class="btn btn-quiet btn-s btn-pill" type="button" id="world-reset">${icon("refresh")} Reset world</button></div></section>
      <section class="w-card"><div class="w-head">${icon("calendar")}<h3>This week</h3><span class="w-count">${esc(w.timezone || "")}</span></div>${days}${later}</section>
      <section class="w-card"><div class="w-head">${icon("inbox")}<h3>Inbox</h3><span class="w-count">${w.unread || 0} unread${newIds.size ? ` · ${newIds.size} new` : ""}</span></div>
        <div>${shown.map((m) => mail(m, "inbox")).join("") || '<p class="w-empty">Empty.</p>'}</div>
        ${inbox.length > 8 ? `<button class="more" type="button" id="inbox-more">${S.inboxAll ? "Show fewer" : `Show all ${inbox.length}`}</button>` : ""}</section>
      <section class="w-card"><div class="w-head">${icon("outbox")}<h3>Sent</h3><span class="w-count">${outbox.length}</span></div>
        <div>${outbox.slice().reverse().map((m) => mail(m, "sent")).join("") || '<p class="w-empty">Nothing sent from this session.</p>'}</div>
        ${drafts.length ? `<div class="w-head" style="margin-top:12px">${icon("doc")}<h3>Drafts</h3><span class="w-count">${drafts.length}</span></div><div>${drafts.map((m) => mail(m, "drafts")).join("")}</div>` : ""}</section>
      <section class="w-card"><div class="w-head">${icon("ticket")}<h3>Bookings and holds</h3><span class="w-count">${bookings.length} booked · ${holds.length} held</span></div>
        <div>${bookings.map((b) => `<div class="kv"><span class="k">${esc(b.id)} · ${esc(b.kind)}${b.status === "cancelled" ? " (cancelled)" : ""}</span><span class="v">$${esc(b.total_usd)}</span><span class="d">${esc(b.option_id)}${b.check_in ? ` · ${esc(b.check_in)} to ${esc(b.check_out)}` : b.date ? ` · ${esc(b.date)}` : ""}${b.hold_id ? ` · from ${esc(b.hold_id)}` : ""}</span></div>`).join("")}
        ${holds.map((h) => `<div class="kv"><span class="k">${icon("hold", "icon")} ${esc(h.hold_id)} · ${esc(h.kind)}</span><span class="v">$${esc(h.total_usd)}</span><span class="d">${esc(h.option_id)} · ${esc(h.status)}</span></div>`).join("")}
        ${!bookings.length && !holds.length ? '<p class="w-empty">No bookings yet.</p>' : ""}</div></section>
      <section class="w-card"><div class="w-head">${icon("doc")}<h3>Briefs and decks</h3><span class="w-count">${docLinks.length}</span></div>
        <div class="links">${docLinks.map((l) => `<a href="${esc(outputUrl(l.path))}" target="_blank" rel="noopener">${icon(l.ic)} ${esc(l.title)} <span class="sub">${esc(l.id)}</span></a>`).join("") || '<p class="w-empty">Nothing saved yet.</p>'}</div></section>
      <section class="w-card"><div class="w-head">${icon("image")}<h3>Browser screenshots</h3><span class="w-count">${links.browser.length}</span></div>
        ${links.browser.length ? `<div class="thumbs">${links.browser.map((s) => `<a href="${esc(outputUrl(s.path))}" target="_blank" rel="noopener"><img loading="lazy" src="${esc(outputUrl(s.path))}" alt="${esc(s.label)}"><span>${esc(s.label)}</span></a>`).join("")}</div>` : '<p class="w-empty">When bookings happen in the browser, its screenshots appear here.</p>'}</section>`;
  }
  async function disconnect(name, input) {
    const label = (S.cfg.connectors.find((c) => c.id === name) || {}).label || name;
    if (!confirm(`Turn off ${label}? The assistant loses it for the rest of this session. Only "Reset world" brings it back.`)) { input.checked = true; return; }
    input.disabled = true;
    try { await api("POST", `/api/sessions/${S.sid}/connectors/${name}`, { connected: false }); toast(`${label} is off for this session.`); }
    catch (err) { toast(err.message); input.checked = true; input.disabled = false; }
    loadWorld();
  }
  async function resetWorld() {
    if (!confirm("Reset Maya's world? You get a fresh run directory and a new conversation. Your setup choices stay.")) return;
    try { await api("POST", `/api/sessions/${S.sid}/reset`); loadWorld(); }
    catch (err) { toast(err.message); }
  }

  // ------------------------------------------------------------------ composer and voice

  function autoGrow() { const t = $("#composer-input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 120) + "px"; }
  function updateSend() {
    const has = $("#composer-input").value.trim().length > 0;
    const btn = $("#send-btn");
    const mic = !has && S.voiceOK;
    btn.classList.toggle("mic", mic);
    btn.disabled = !has && !mic;
    $("#send-glyph").setAttribute("href", `${ICONS}#i-${mic ? "mic" : "send"}`);
    btn.setAttribute("aria-label", mic ? "Dictate a message" : "Send");
    btn.title = mic ? "Dictate. Some browsers send the audio to their maker to recognise it." : "Send";
  }
  async function sendText(text) {
    text = (text || "").trim(); if (!text || !S.sid) return;
    try {
      await api("POST", `/api/sessions/${S.sid}/messages`, { text });
      $("#composer-input").value = ""; autoGrow(); updateSend();
      $("#plus-btn").setAttribute("aria-expanded", "false");
      renderSuggestions(false);
    } catch (err) { toast(err.message); }
  }
  async function initVoice() {
    if (!S.cfg?.voice?.enabled) return;
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    let brave = false;
    try { brave = !!(navigator.brave && (await navigator.brave.isBrave())); } catch { brave = false; }
    S.voiceOK = !!SR && !brave; // Brave exposes the API but can't recognise speech.
    if ("speechSynthesis" in window) $("#speak-btn").hidden = false;
    updateSend();
  }
  function dictate() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return;
    const btn = $("#send-btn");
    if (S.rec) { S.rec.stop(); return; }
    const rec = new SR(); S.rec = rec;
    rec.lang = navigator.language || "en-US"; rec.interimResults = true; rec.continuous = false;
    const input = $("#composer-input"); const base = input.value.trim();
    rec.onresult = (ev) => {
      let t = ""; for (let i = 0; i < ev.results.length; i++) t += ev.results[i][0].transcript;
      input.value = (base ? base + " " : "") + t; autoGrow();
    };
    rec.onerror = (ev) => { toast(`Dictation stopped (${ev.error}).`); };
    rec.onend = () => { S.rec = null; btn.classList.remove("listening"); updateSend(); input.focus(); };
    btn.classList.add("listening");
    try { rec.start(); } catch { S.rec = null; btn.classList.remove("listening"); }
  }

  // ------------------------------------------------------------------ views

  function showView(v) {
    if (PHONE) v = "chat";
    S.view = v;
    document.body.dataset.view = v;
    $("#landing").hidden = v !== "landing";
    $("#workspace").hidden = v !== "chat";
    $("#nav-cta").hidden = v === "chat";
    $("#nav-new").hidden = v !== "chat";
    $("#nav-session").hidden = v !== "chat";
    $("#hero-resume").hidden = !(v === "landing" && S.sid);
    $("#start-btn").textContent = S.sid ? "Start a new session" : "Start messaging";
    if (v === "chat") { closeSheet(); setTimeout(() => $("#composer-input").focus({ preventScroll: true }), 50); }
  }
  function openSheet() { $("#bts").classList.add("open"); $("#sheet-backdrop").hidden = false; }
  function closeSheet() { $("#bts").classList.remove("open"); $("#sheet-backdrop").hidden = true; }
  function selectTab(which) {
    for (const t of ["trace", "world"]) {
      $(`#tab-${t}`).setAttribute("aria-selected", String(t === which));
      $(`#panel-${t}`).hidden = t !== which;
    }
    if (which === "world") { $("#world-dot").hidden = true; loadWorld(true); }
  }

  // ------------------------------------------------------------------ setup

  function setupInit() {
    const c = S.cfg;
    document.title = PHONE ? `${c.app_name} · phone` : c.app_name;
    for (const n of document.querySelectorAll("[data-app-name]")) n.textContent = c.app_name;
    const p = c.persona;
    $("#persona-initials").textContent = p.initials;
    $("#persona-name").textContent = p.name;
    $("#persona-role").textContent = `${p.title} at ${p.company}`;
    $("#persona-meta").textContent = [p.email, p.timezone && p.timezone.split("/").pop().replace("_", " ") + " time"].filter(Boolean).join(" · ");
    let saved = {};
    try { saved = JSON.parse(store.get("intuition:setup") || "{}") || {}; } catch { saved = {}; }
    $("#connector-list").innerHTML = c.connectors.map((x) => `<li>
      <span class="conn-icon">${icon(CONN_ICON[x.id] || "plug")}</span>
      <span class="conn-text"><span class="conn-name">${esc(x.label)}</span><span class="conn-desc">${esc(x.sees)} ${esc(x.does)}</span></span>
      <label class="switch"><input type="checkbox" role="switch" data-connector="${esc(x.id)}" ${saved.connectors?.[x.id] ? "checked" : ""}><span class="track" aria-hidden="true"></span><span class="sr">Connect ${esc(x.label)}</span></label>
    </li>`).join("");
    updateConnectAll();
    // harness
    const hs = c.harnesses;
    if (hs.length > 1) {
      $("#opt-harness-row").hidden = false;
      const def = saved.harness && hs.find((h) => h.id === saved.harness && h.available) ? saved.harness : c.default_harness;
      $("#opt-harness").innerHTML = hs.map((h) => `<label><input type="radio" name="harness" value="${esc(h.id)}" ${h.id === def ? "checked" : ""} ${h.available ? "" : "disabled"}><span>${esc(h.label)}</span></label>`).join("");
    } else {
      $("#opt-harness").innerHTML = `<label><input type="radio" name="harness" value="${esc(hs[0].id)}" checked><span>${esc(hs[0].label)}</span></label>`;
    }
    // search
    const live = c.search.live_available;
    const sLive = document.querySelector('input[name="search"][value="live"]');
    sLive.disabled = !live;
    document.querySelector(`input[name="search"][value="${live && saved.search !== "mock" ? "live" : "mock"}"]`).checked = true;
    // model
    $("#opt-model").innerHTML = c.models.map((m) => `<label><input type="radio" name="model" value="${esc(m.id)}" ${(saved.model || c.default_model) === m.id ? "checked" : ""}><span>${esc(m.label)}</span></label>`).join("");
    // browser
    const b = $("#opt-browser");
    b.disabled = !c.browser.available;
    b.checked = c.browser.available && (saved.browser ?? c.browser.default);
    $("#browser-note").textContent = c.browser.available ? "Opens Brave so you can watch it fill in the booking sites. " + c.browser.message : c.browser.message;
    $("#opt-auto").checked = saved.auto_approve ?? true;
    // guide link
    const g = $("#guide-link");
    if (!c.guide.available) { g.removeAttribute("href"); g.textContent = "Workshop guide: open site/dist/index.html in the kit"; }
    updateSearchNote(); updateHarnessNote();
  }
  function updateConnectAll() {
    const boxes = [...document.querySelectorAll("[data-connector]")];
    const all = boxes.every((x) => x.checked);
    const btn = $("#connect-all");
    btn.textContent = all ? "All connected" : "Connect all";
    btn.disabled = all;
  }
  function updateSearchNote() {
    const live = document.querySelector('input[name="search"]:checked')?.value === "live";
    $("#search-note").textContent = !S.cfg.search.live_available
      ? "Recorded results (add SERPAPI_API_KEY to .env for live search)."
      : live ? "Live web results. The fictional companies won't appear; evals always use recorded results." : "Recorded results, the same ones the evals use.";
  }
  function selectedHarness() { return document.querySelector('input[name="harness"]:checked')?.value || S.cfg.default_harness; }
  function updateHarnessNote() {
    const h = S.cfg.harnesses.find((x) => x.id === selectedHarness());
    $("#harness-note").textContent = h?.id === "fake" ? h.note : "Claude Code in headless mode, with your own login.";
    const notice = $("#claude-notice");
    const cl = S.cfg.claude;
    const needs = selectedHarness() === "claude_code";
    let msg = "";
    if (needs && (!cl.installed || cl.logged_in === false)) msg = cl.message || "Claude Code isn't ready.";
    notice.hidden = !msg && !(needs && cl.logged_in == null && cl.installed);
    notice.className = "notice" + (msg ? " bad" : "");
    notice.innerHTML = msg ? `<b>Claude Code isn't ready.</b> ${inline(msg)}${S.cfg.harnesses.some((x) => x.id === "fake") ? " Or pick the scripted demo above." : ""}`
      : `Couldn't confirm the Claude Code login. If your first message fails, run <code>claude</code> once in a terminal and log in.`;
    $("#start-btn").disabled = !!msg;
  }
  function saveSetup(body) { store.set("intuition:setup", JSON.stringify({ connectors: body.connectors, harness: body.harness, search: body.search_mode, model: body.model, browser: body.browser, auto_approve: body.auto_approve })); }
  async function startSession() {
    const connectors = {};
    for (const x of document.querySelectorAll("[data-connector]")) connectors[x.dataset.connector] = x.checked;
    if (!Object.values(connectors).some(Boolean) && !confirm("Nothing is connected, so the assistant can't see your inbox or calendar. Start anyway?")) return;
    const body = {
      harness: selectedHarness(),
      search_mode: document.querySelector('input[name="search"]:checked')?.value || "mock",
      model: document.querySelector('input[name="model"]:checked')?.value || "opus",
      browser: $("#opt-browser").checked,
      auto_approve: $("#opt-auto").checked,
      connectors,
    };
    saveSetup(body);
    const btn = $("#start-btn"); btn.disabled = true; $("#start-error").textContent = "";
    const label = btn.textContent; btn.textContent = "Starting…";
    try {
      if (S.sid) { bringBack(); try { await api("DELETE", `/api/sessions/${S.sid}`); } catch { /* already gone */ } }
      const r = await api("POST", "/api/sessions", body);
      S.sid = r.session_id; store.set("intuition:session", S.sid);
      S.events = []; chatClear(); traceClear(); S.world = null;
      showView("chat");
      connect();
      window.scrollTo(0, 0);
    } catch (err) {
      $("#start-error").textContent = err.message;
    } finally { btn.disabled = false; btn.textContent = S.sid ? "Start a new session" : label; }
  }
  async function newSession() {
    if (!confirm("Start a new session? This conversation ends and the next one starts in a fresh world.")) return;
    bringBack();
    try { if (S.sid) await api("DELETE", `/api/sessions/${S.sid}`); } catch { /* gone */ }
    if (S.es) S.es.close();
    S.sid = null; store.set("intuition:session", null);
    showView("landing");
    $("#setup").scrollIntoView();
  }

  // ------------------------------------------------------------------ pop-out phone

  let popup = null, popPoll = 0;
  function setFull() {
    const q = PHONE ? "(max-width: 409px), (max-height: 699px)" : "(max-width: 860px)";
    document.body.classList.toggle("phone-full", matchMedia(q).matches);
  }
  function setPopped(on) {
    if (PHONE) return;
    document.body.classList.toggle("popped", on);
    $("#popout-bar").hidden = !on;
    $("#phone").hidden = on;
    clearInterval(popPoll);
    if (on && popup) popPoll = setInterval(() => { if (!popup || popup.closed) { popup = null; setPopped(false); } }, 1000);
    if (!on) { const b = $("#chat-body"); b.scrollTop = b.scrollHeight; }
  }
  function popOut() {
    if (!S.sid) return;
    const url = `/phone/${encodeURIComponent(S.sid)}`;
    if (popup && !popup.closed) { popup.focus(); return; }
    const w = window.open(url, "intuition-phone", "width=430,height=880");
    if (!w) { toast(`Your browser blocked the pop-up. Allow pop-ups here, or open ${location.origin}${url}`); return; }
    popup = w;
    try { w.focus(); } catch { /* ignore */ }
    setPopped(true);
  }
  function bringBack() {
    if (popup && !popup.closed) popup.close();
    else if (chan) chan.postMessage({ type: "close-request", sid: S.sid });
    popup = null;
    setPopped(false);
  }
  if (chan) {
    chan.onmessage = (ev) => {
      const m = ev.data || {};
      if (!S.sid || m.sid !== S.sid) return;
      if (PHONE) {
        if (m.type === "close-request") window.close();
        if (m.type === "who") chan.postMessage({ type: "here", sid: S.sid });
      } else {
        if (m.type === "open" || m.type === "here") setPopped(true);
        if (m.type === "closed") { popup = null; setPopped(false); }
      }
    };
  }
  window.addEventListener("pagehide", () => { if (PHONE && chan && S.sid) chan.postMessage({ type: "closed", sid: S.sid }); });

  // ------------------------------------------------------------------ theme

  const THEMES = ["auto", "light", "dark"];
  function applyTheme(t) {
    if (t === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
    const b = $("#theme-btn");
    b.innerHTML = icon(t === "auto" ? "auto" : t === "light" ? "sun" : "moon");
    b.setAttribute("aria-label", `Theme: ${t === "auto" ? "match the system" : t}. Change theme`);
  }

  // ------------------------------------------------------------------ wiring

  function wire() {
    document.addEventListener("click", (ev) => {
      const a = ev.target.closest("[data-action='get-started']");
      if (a) { ev.preventDefault(); if (S.view !== "landing") showView("landing"); $("#setup").scrollIntoView({ behavior: "smooth" }); setTimeout(() => $("#connect-all").focus({ preventScroll: true }), 500); return; }
      if (ev.target.closest("[data-action='home']")) { ev.preventDefault(); if (S.view === "chat") return; window.scrollTo({ top: 0, behavior: "smooth" }); return; }
      const d = ev.target.closest("[data-decide]");
      if (d) { decide(d.closest(".approval"), d.dataset.decide); return; }
      const h = ev.target.closest(".hood-toggle");
      if (h) { toggleHood(h.dataset.turn); return; }
      const st = ev.target.closest(".step-line");
      if (st) { const g = st.closest(".turn-group"); const t = [...S.turns.values()].find((x) => x.group === g); if (t) toggleStep(t, st); return; }
      const sg = ev.target.closest(".suggestion");
      if (sg) { sendText(sg.textContent); return; }
      if (ev.target.closest("#world-reset")) { resetWorld(); return; }
      if (ev.target.closest("#inbox-more")) { S.inboxAll = !S.inboxAll; renderWorld(); return; }
      if (!ev.target.closest("#menu") && !ev.target.closest("#menu-btn")) { $("#menu").hidden = true; $("#menu-btn").setAttribute("aria-expanded", "false"); }
    });
    document.addEventListener("change", (ev) => {
      const t = ev.target;
      if (t.matches("[data-connector]")) updateConnectAll();
      if (t.matches('input[name="search"]')) updateSearchNote();
      if (t.matches('input[name="harness"]')) updateHarnessNote();
      if (t.matches("[data-conn]") && !t.checked) disconnect(t.dataset.conn, t);
    });
    $("#world").addEventListener("toggle", (ev) => {
      const d = ev.target; if (!(d instanceof HTMLDetailsElement) || !d.dataset.mail) return;
      if (d.open) S.openMail.add(d.dataset.mail); else S.openMail.delete(d.dataset.mail);
    }, true);
    $("#connect-all").addEventListener("click", () => { for (const x of document.querySelectorAll("[data-connector]")) x.checked = true; updateConnectAll(); });
    $("#start-btn").addEventListener("click", startSession);
    $("#hero-resume").addEventListener("click", () => showView("chat"));
    $("#nav-new").addEventListener("click", newSession);
    $("#menu-new").addEventListener("click", newSession);
    $("#menu-reset").addEventListener("click", () => { $("#menu").hidden = true; resetWorld(); });
    $("#menu-btn").addEventListener("click", () => { const m = $("#menu"); m.hidden = !m.hidden; $("#menu-btn").setAttribute("aria-expanded", String(!m.hidden)); });
    $("#menu-auto").addEventListener("change", async (ev) => {
      try { await api("POST", `/api/sessions/${S.sid}/options`, { auto_approve: ev.target.checked }); toast(`Auto-approve after a yes is ${ev.target.checked ? "on" : "off"}.`); }
      catch (err) { ev.target.checked = !ev.target.checked; toast(err.message); }
    });
    $("#chat-back").addEventListener("click", () => {
      if (PHONE) { if (chan) chan.postMessage({ type: "closed", sid: S.sid }); window.close(); return; }
      showView("landing"); window.scrollTo(0, 0);
    });
    $("#popout-btn").addEventListener("click", popOut);
    $("#popout-back").addEventListener("click", bringBack);
    window.addEventListener("resize", setFull);
    $("#bts-open").addEventListener("click", openSheet);
    $("#sheet-close").addEventListener("click", closeSheet);
    $("#sheet-backdrop").addEventListener("click", closeSheet);
    $("#tab-trace").addEventListener("click", () => selectTab("trace"));
    $("#tab-world").addEventListener("click", () => selectTab("world"));
    $(".tabs").addEventListener("keydown", (ev) => {
      if (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft") return;
      const next = $("#tab-trace").getAttribute("aria-selected") === "true" ? "world" : "trace";
      selectTab(next); $(`#tab-${next}`).focus();
    });
    $("#plus-btn").addEventListener("click", () => {
      const b = $("#plus-btn"); b.setAttribute("aria-expanded", String(b.getAttribute("aria-expanded") !== "true"));
      renderSuggestions(S.messages.size === 0);
    });
    const input = $("#composer-input");
    input.addEventListener("input", () => { autoGrow(); updateSend(); });
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); sendText(input.value); }
    });
    $("#composer").addEventListener("submit", (ev) => {
      ev.preventDefault();
      if ($("#send-btn").classList.contains("mic") || S.rec) dictate(); else sendText(input.value);
    });
    $("#speak-btn").addEventListener("click", () => {
      S.speak = !S.speak;
      const b = $("#speak-btn");
      b.setAttribute("aria-pressed", String(S.speak));
      b.innerHTML = icon(S.speak ? "speaker" : "speaker-off");
      if (!S.speak && "speechSynthesis" in window) speechSynthesis.cancel();
    });
    const body = $("#chat-body");
    body.addEventListener("scroll", () => { if (nearBottom(body)) $("#chat-jump").hidden = true; });
    $("#chat-jump").addEventListener("click", () => { body.scrollTop = body.scrollHeight; $("#chat-jump").hidden = true; });
    $("#theme-btn").addEventListener("click", () => {
      const cur = document.documentElement.dataset.theme || "auto";
      const next = THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length];
      store.set("intuition:theme", next === "auto" ? null : next);
      applyTheme(next);
    });
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") { closeSheet(); $("#menu").hidden = true; } });
  }

  async function boot() {
    applyTheme(document.documentElement.dataset.theme || "auto");
    setFull();
    wire();
    traceInit();
    if (PHONE) $("#chat-back").setAttribute("aria-label", "Close this window");
    try { S.cfg = await api("GET", "/api/config"); }
    catch (err) { $("#start-error").textContent = `The app's server didn't answer: ${err.message}`; return; }
    setupInit();
    await initVoice();
    updateSend();
    if (PHONE) {
      S.sid = document.body.dataset.session;
      showView("chat");
      connect();
      if (chan) chan.postMessage({ type: "open", sid: S.sid });
      return;
    }
    const saved = store.get("intuition:session");
    if (saved) {
      try {
        S.info = await api("GET", `/api/sessions/${encodeURIComponent(saved)}`);
        S.sid = saved;
        showView("chat");
        connect();
        if (chan) chan.postMessage({ type: "who", sid: S.sid });
        return;
      } catch { store.set("intuition:session", null); }
    }
    showView("landing");
  }
  boot();
})();
