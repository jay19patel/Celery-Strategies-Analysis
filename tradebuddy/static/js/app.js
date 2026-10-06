/* TradeBuddy shared client: API, live event stream, formatting, header, modal, toasts. */
(() => {
  const $ = (id) => document.getElementById(id);
  const handlers = {};
  let token = "";
  try { token = localStorage.getItem("tb_token") || ""; } catch { /* storage blocked */ }

  // ── formatting ──────────────────────────────────────────────────────────
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v, d = 2) => (v === null || v === undefined || v === "" || Number.isNaN(Number(v)) ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: d }));
  const price = (v) => (v === null || v === undefined ? "—" : num(v, Math.abs(v) >= 100 ? 2 : Math.abs(v) >= 1 ? 4 : 6));
  const money = (v, d = 2) => (v === null || v === undefined ? "—" : `${v < 0 ? "-" : ""}$${num(Math.abs(v), d)}`);
  const signed = (v, d = 2) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : v < 0 ? "-" : ""}$${num(Math.abs(v), d)}`);
  const pnlClass = (v) => (v > 0 ? "up" : v < 0 ? "down" : "muted");
  const pct = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`);
  const compact = (v) => (v === null || v === undefined ? "—" : Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(v));
  // A counter that may run for a year: 950 · 1.1K · 14.7K · 15M, exact value on hover.
  const count = (v) => (v === null || v === undefined ? "—"
    : `<span title="${Number(v).toLocaleString("en-US")}">${Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v)}</span>`);
  const ms = (v) => (v === null || v === undefined ? "—" : v >= 1000 ? `${num(v / 1000, 2)} s` : `${num(v, v >= 100 ? 0 : 1)} ms`);
  // A gauge from SL (0%) through entry to TP (100%), with the mark as a needle.
  function levelsBar(p) {
    if (!p.stop_loss || !p.take_profit) return '<span class="muted text-[11px]">no SL/TP</span>';
    const lo = Math.min(p.stop_loss, p.take_profit), hi = Math.max(p.stop_loss, p.take_profit), span = hi - lo || 1;
    const at = (v) => Math.max(0, Math.min(100, ((v - lo) / span) * 100));
    const long = p.side === "long", moved = p.mark_price - p.entry_price;
    const toTp = (moved / (p.take_profit - p.entry_price)) * 100, toSl = (moved / (p.stop_loss - p.entry_price)) * 100;
    return `<div class="lv" title="SL ${price(p.stop_loss)} · entry ${price(p.entry_price)} · TP ${price(p.take_profit)}">
      <div class="lv-track ${long ? "" : "rev"}"></div>
      <span class="lv-entry" style="left:${at(p.entry_price)}%"></span>
      <span class="lv-mark ${toTp >= 0 ? "pos" : "neg"}" style="left:${at(p.mark_price)}%"></span>
    </div><div class="text-[10px] muted mt-0.5">${toTp >= 0 ? `${num(toTp, 0)}% of the way to target` : `${num(toSl, 0)}% of the way to stop`}</div>`;
  }
  const time = (ts) => (ts ? new Date(ts * 1000).toLocaleTimeString("en-GB") : "—");
  const dateTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—");
  const ago = (ts) => {
    if (!ts) return "never";
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 5) return "just now";
    if (s < 60) return `${Math.floor(s)}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  };
  const duration = (seconds) => {
    const s = Math.max(0, Math.floor(seconds));
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m ${s % 60}s`;
  };
  const side = (v) => `<span class="side-${esc(v)}">${esc(String(v || "").toUpperCase())}</span>`;
  const STATUS_BADGE = { filled: "badge-green", open: "badge-blue", pending: "badge-amber", unknown: "badge-amber", rejected: "badge-red", cancelled: "" };
  const status = (v) => `<span class="badge ${STATUS_BADGE[v] ?? ""}">${esc(v)}</span>`;
  const EVENT_BADGE = {
    SignalGenerated: "badge-blue", OrderPlaced: "badge-green", PositionClosed: "badge-green", TradeSkipped: "badge-amber",
    OrderUnknown: "badge-amber", OrderFailed: "badge-red", StrategyError: "badge-red", SettingsChanged: "badge-blue", ToggleChanged: "",
    ProtectionTrailed: "badge-green", DailyLossHalt: "badge-solid-red", GuardAlert: "badge-red",
  };
  const eventBadge = (t) => `<span class="badge ${EVENT_BADGE[t] ?? ""}">${esc(t)}</span>`;

  // ── API ─────────────────────────────────────────────────────────────────
  async function api(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json", "X-API-Token": token },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) {
      const entered = await ask({ title: "API token required", body: "This action is protected. Enter the API_TOKEN the server was started with.", input: "password", ok: "Save token" });
      if (entered) {
        token = entered;
        try { localStorage.setItem("tb_token", token); } catch { /* ignore */ }
        return api(method, url, body);
      }
    }
    if (!res.ok) {
      const msg = data.detail || `HTTP ${res.status}`;
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return data;
  }
  const get = (url) => api("GET", url);
  const post = (url, body = {}) => api("POST", url, body);
  const put = (url, body = {}) => api("PUT", url, body);

  // ── live events ─────────────────────────────────────────────────────────
  function on(types, fn) {
    for (const t of [].concat(types)) (handlers[t] ||= []).push(fn);
  }
  function emit(e) {
    for (const fn of [...(handlers[e.type] || []), ...(handlers["*"] || [])]) {
      try { fn(e); } catch (err) { console.error(err); }
    }
  }
  function connect() {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => setDot("sbLive", true);
    ws.onclose = () => { setDot("sbLive", false); setTimeout(connect, 2000); };
    ws.onmessage = (m) => { try { emit(JSON.parse(m.data)); } catch (err) { console.error(err); } };
  }

  // ── toasts and modal ────────────────────────────────────────────────────
  function toast(msg, kind = "") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = msg;
    $("toasts").append(el);
    setTimeout(() => el.remove(), 4000);
  }
  function ask({ title, body = "", ok = "Confirm", danger = false, input = null, placeholder = "", value = "" }) {
    return new Promise((resolve) => {
      const modal = $("modal"), field = $("modalInput"), okBtn = $("modalOk");
      $("modalTitle").textContent = title;
      $("modalBody").innerHTML = body;
      okBtn.textContent = ok;
      okBtn.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;
      field.classList.toggle("hidden", !input);
      field.type = input || "text";
      field.placeholder = placeholder;
      field.value = value;
      modal.classList.add("open");
      if (input) setTimeout(() => field.focus(), 30);
      const done = (result) => {
        modal.classList.remove("open");
        okBtn.onclick = $("modalCancel").onclick = field.onkeydown = null;
        resolve(result);
      };
      okBtn.onclick = () => done(input ? field.value : true);
      $("modalCancel").onclick = () => done(input ? null : false);
      field.onkeydown = (ev) => { if (ev.key === "Enter") okBtn.click(); if (ev.key === "Escape") $("modalCancel").click(); };
    });
  }

  // ── rendering helpers ───────────────────────────────────────────────────
  function rows(tbody, items, render, empty = "Nothing here yet", colspan = 20) {
    const el = typeof tbody === "string" ? $(tbody) : tbody;
    el.innerHTML = items.length ? items.map(render).join("") : `<tr><td class="empty" colspan="${colspan}">${esc(empty)}</td></tr>`;
  }
  const switchHtml = (key, checked, sm = false) =>
    `<label class="switch ${sm ? "sm" : ""}"><input type="checkbox" data-toggle="${esc(key)}" ${checked ? "checked" : ""}><span class="track"></span></label>`;
  function bindToggles(root = document) {
    root.querySelectorAll("input[data-toggle]").forEach((el) => {
      el.onchange = async () => {
        try { await post("/api/toggles", { key: el.dataset.toggle, enabled: el.checked }); }
        catch (err) { el.checked = !el.checked; toast(err.message, "error"); }
      };
    });
  }
  function icons() { if (window.lucide) lucide.createIcons(); }
  function setDot(id, on) { const el = $(id); if (el) el.className = `dot ${on ? "on" : "off"}`; }

  // ── header ──────────────────────────────────────────────────────────────
  let header = null;
  function brokerLabel(b) {
    if (b.name === "paper") return { text: `PAPER · ${b.env.toUpperCase()} PRICES`, cls: "badge-blue" };
    if (b.real_money) return { text: "DELTA · LIVE MONEY", cls: "badge-solid-red" };
    return { text: "DELTA · DEMO", cls: "badge-amber" };
  }
  function renderHeader(h) {
    header = h;
    $("brokerSwitches").innerHTML = h.brokers.map((b) => {
      const label = brokerLabel(b);
      const warn = b.not_ready ? `<i data-lucide="triangle-alert" class="w-3.5 h-3.5 text-amber-500"></i>` : "";
      return `<div class="flex items-center gap-2 border border-slate-200 rounded-lg pl-2 pr-2.5 py-1 bg-white" title="${esc(b.not_ready || `Trading on ${b.name}`)}">
        <span class="badge ${label.cls}">${esc(label.text)}</span>${warn}
        <label class="switch sm"><input type="checkbox" data-broker-switch="${esc(b.name)}" ${b.trading ? "checked" : ""}><span class="track"></span></label>
        <span class="text-[11px] font-semibold w-6 ${b.trading ? "text-emerald-600" : "text-slate-400"}">${b.trading ? "ON" : "OFF"}</span>
      </div>`;
    }).join("");
    $("brokerSwitches").querySelectorAll("input[data-broker-switch]").forEach((el) => (el.onchange = () => switchBroker(el)));
    setDot("sbFeed", h.feed_connected);
    $("sbFeedText").textContent = h.feed_connected ? `Market stream · ${h.data_env}` : "Market stream down";
    const delta = h.brokers.some((b) => b.name === "delta");
    setDot("sbPrivate", h.feed_authenticated);
    if (!delta) $("sbPrivate").className = "dot";
    $("sbPrivateText").textContent = delta ? (h.feed_authenticated ? "Delta private channels" : "Delta private channels off") : "Delta not active";
    const now = Date.now();
    for (const [sym, p] of Object.entries(h.prices)) if (p.fresh) lastTickAt[sym] ??= now - p.age_seconds * 1000;
    renderMarquee(h.prices);
    icons();
  }
  async function switchBroker(el) {
    const name = el.dataset.brokerSwitch, enabled = el.checked;
    const b = header?.brokers.find((x) => x.name === name);
    if (enabled && b?.real_money) {
      const ok = await ask({ title: "Enable REAL-MONEY trading?", body: "Signals will place real orders on your Delta live account.", ok: "Enable live trading", danger: true });
      if (!ok) { el.checked = false; return; }
    }
    try { await post("/api/toggles", { key: `trading:${name}`, enabled }); } catch (err) { el.checked = !enabled; toast(err.message, "error"); }
    loadHeader();
  }
  // ── price marquee ───────────────────────────────────────────────────────
  // Scrolls like a news ticker. The track holds the list twice and slides by half its width, so the
  // loop is seamless. A price with no tick for 30s is shown as STALE, never as current (invariant 8).
  const STALE_MS = 30000;
  const lastPrice = {}, lastTickAt = {}, stats = {};
  let marqueeSymbols = "";
  function renderMarquee(prices) {
    const box = $("marquee"), track = $("marqueeTrack");
    if (!box) return;
    const syms = Object.keys(prices).sort();
    box.classList.toggle("hidden", !syms.length);
    if (!syms.length) return;
    if (syms.join(",") !== marqueeSymbols) {
      marqueeSymbols = syms.join(",");
      const repeat = Math.max(1, Math.ceil(8 / syms.length));  // enough items to fill a wide screen
      const items = Array.from({ length: repeat }, () => syms).flat().map((s) =>
        `<span class="marquee-item" data-sym="${esc(s)}"><span class="sym">${esc(s)}</span><span class="px">—</span><span class="chg"></span></span>`).join("");
      track.innerHTML = items + items;
      track.style.setProperty("--marquee-duration", `${Math.max(25, repeat * syms.length * 5)}s`);
    }
    for (const s of syms) {
      if (prices[s].stats) stats[s] = prices[s].stats;
      paintMarquee(s, prices[s].stats?.last ?? prices[s].price);
    }
  }
  function paintMarquee(sym, p) {
    const prev = lastPrice[sym];
    lastPrice[sym] = p;
    const stale = !lastTickAt[sym] || Date.now() - lastTickAt[sym] > STALE_MS;
    const dir = prev === undefined || p === prev ? "" : p > prev ? "up" : "down";
    const raw = stats[sym]?.change_24h_pct;  // the exchange's own 24h change on the last price
    const chg = raw === null || raw === undefined ? null : Math.abs(raw) < 0.005 ? 0 : raw;  // never "-0.00%"
    $("marqueeTrack").querySelectorAll(`[data-sym="${CSS.escape(sym)}"]`).forEach((item) => {
      item.classList.toggle("stale", stale);
      const px = item.querySelector(".px");
      px.textContent = price(p);
      if (dir) {
        px.classList.remove("up", "down", "flash-up", "flash-down");
        void px.offsetWidth;  // restart the flash animation
        px.classList.add(dir, `flash-${dir}`);
      }
      const c = item.querySelector(".chg");
      c.className = `chg ${stale || chg === null ? "" : chg > 0 ? "up" : chg < 0 ? "down" : ""}`;
      c.textContent = stale ? "STALE" : chg === null ? "" : `${chg > 0 ? "▲ +" : chg < 0 ? "▼ " : ""}${chg.toFixed(2)}% 24h`;
      c.title = stale ? "No price for 30s+ — not current" : "24h change of the last traded price, as Delta reports it";
    });
  }
  setInterval(() => { for (const s of Object.keys(lastPrice)) paintMarquee(s, lastPrice[s]); }, 5000);
  async function loadHeader() {
    try { renderHeader(await get("/api/header")); } catch (err) { console.error(err); }
  }

  function initChrome() {
    $("closeAllBtn").onclick = async () => {
      const ok = await ask({ title: "Close everything?", body: `Trading is switched off on every broker, then every open position on <b>${esc((header?.brokers || []).map((b) => b.name).join(" and ") || "the active brokers")}</b> is closed at market.`, ok: "Close all", danger: true });
      if (!ok) return;
      try { const r = await post("/api/close-all"); toast(`Closed ${r.closed.length} position(s)${r.errors.length ? `, ${r.errors.length} error(s)` : ""}`, r.errors.length ? "error" : "ok"); }
      catch (err) { toast(err.message, "error"); }
      loadHeader();
    };
    const sidebar = $("sidebar"), shade = $("sidebarShade");
    const open = (v) => { sidebar.classList.toggle("-translate-x-full", !v); shade.classList.toggle("hidden", !v); };
    $("menuBtn").onclick = () => open(true);
    shade.onclick = () => open(false);

    on(["ToggleChanged", "SettingsChanged", "FeedStatus"], loadHeader);
    on("Tick", (e) => {
      if (!header) return;
      const isNew = !(e.symbol in header.prices);
      header.prices[e.symbol] = { price: e.price, age_seconds: 0, fresh: true };
      lastTickAt[e.symbol] = Date.now();
      if (isNew) renderMarquee(header.prices); else if (!stats[e.symbol]) paintMarquee(e.symbol, e.price);
    });
    on("MarketStats", (e) => {
      stats[e.symbol] = e;
      if (header?.prices[e.symbol]) header.prices[e.symbol].stats = e;
      lastTickAt[e.symbol] = Date.now();
      if (e.last != null && $("marqueeTrack")?.querySelector(`[data-sym="${CSS.escape(e.symbol)}"]`)) paintMarquee(e.symbol, e.last);
    });
    on("SettingsChanged", (e) => { if (e.trading_stopped) toast("Delta settings changed — Delta trading switched off"); });
    on("OrderFailed", (e) => toast(`Order failed: ${e.error}`, "error"));
    on("OrderUnknown", (e) => toast(`Order outcome unknown — looking it up: ${e.error}`, "error"));
    on("OrderPlaced", (e) => toast(`Order ${e.status}: ${e.client_order_id}`, "ok"));
    on("ProtectionTrailed", (e) => toast(`${e.symbol} on ${e.broker} trailed (${e.step}/${e.max_steps}): SL ${price(e.stop_loss)} · TP ${price(e.take_profit)}`, "ok"));
    on("DailyLossHalt", (e) => toast(`${e.broker}: daily loss limit hit (${num(e.loss_pct, 2)}%). Positions closed; no new entries today.`, "error"));
    on("GuardAlert", (e) => toast(`${e.broker} ${e.symbol}: ${e.message}`, "error"));
    on("PositionClosed", (e) => toast(`${e.symbol} closed (${e.reason}) ${signed(e.pnl, 4)}`, e.pnl >= 0 ? "ok" : "error"));
  }

  window.TB = {
    $, esc, num, price, money, signed, pnlClass, pct, compact, count, ms, levelsBar, stats, time, dateTime, ago, duration, side, status, eventBadge,
    api, get, post, put, on, toast, ask, rows, switchHtml, bindToggles, icons, loadHeader,
    get header() { return header; },
  };

  document.addEventListener("DOMContentLoaded", () => {
    initChrome();
    loadHeader();
    setInterval(loadHeader, 10000);
    connect();
  });
})();
