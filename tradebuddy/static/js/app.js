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
  function brokerLabel(h) {
    if (h.broker === "paper") return { text: `PAPER · ${h.data_env.toUpperCase()} PRICES`, cls: "badge-blue" };
    if (h.is_real_money) return { text: "DELTA · LIVE MONEY", cls: "badge-solid-red" };
    return { text: "DELTA · DEMO", cls: "badge-amber" };
  }
  function renderHeader(h) {
    header = h;
    const b = brokerLabel(h);
    $("brokerBadge").textContent = b.text;
    $("brokerBadge").className = `badge ${b.cls}`;
    $("brokerWarn").classList.toggle("hidden", !h.broker_ready);
    $("brokerWarn").textContent = h.broker_ready;
    $("tradingSwitch").checked = h.trading;
    $("tradingText").textContent = h.trading ? "Trading ON" : "Trading OFF";
    $("tradingText").className = `text-xs font-semibold w-20 ${h.trading ? "text-emerald-600" : "text-slate-500"}`;
    setDot("sbFeed", h.feed_connected);
    $("sbFeedText").textContent = h.feed_connected ? `Market stream · ${h.data_env}` : "Market stream down";
    setDot("sbPrivate", h.feed_authenticated);
    if (h.broker !== "delta") $("sbPrivate").className = "dot";
    $("sbPrivateText").textContent = h.broker === "delta" ? (h.feed_authenticated ? "Private channels" : "Private channels off") : "Private channels (Delta only)";
    renderTicker(h.prices);
  }
  const lastPrice = {};
  function renderTicker(prices) {
    $("tickerStrip").innerHTML = Object.entries(prices).sort().map(([sym, p]) => {
      const cls = lastPrice[sym] === undefined ? "" : p.price > lastPrice[sym] ? "up" : p.price < lastPrice[sym] ? "down" : "";
      lastPrice[sym] = p.price;
      return `<span>${esc(sym)} <b class="${p.fresh ? cls : "down"}">${price(p.price)}</b></span>`;
    }).join("");
    $("tickerStrip").classList.toggle("hidden", !Object.keys(prices).length);
    $("tickerStrip").classList.toggle("md:flex", !!Object.keys(prices).length);
  }
  async function loadHeader() {
    try { renderHeader(await get("/api/header")); } catch (err) { console.error(err); }
  }

  function initChrome() {
    $("tradingSwitch").onchange = async (ev) => {
      const enabled = ev.target.checked;
      if (enabled && header?.is_real_money) {
        const ok = await ask({ title: "Enable REAL-MONEY trading?", body: "Signals will place real orders on your Delta live account.", ok: "Enable live trading", danger: true });
        if (!ok) { ev.target.checked = false; return; }
      }
      try { await post("/api/toggles", { key: "trading", enabled }); } catch (err) { ev.target.checked = !enabled; toast(err.message, "error"); }
      loadHeader();
    };
    $("closeAllBtn").onclick = async () => {
      const ok = await ask({ title: "Close everything?", body: `Trading is switched off, then every open position on the <b>${esc(header?.broker ?? "active")}</b> broker is closed at market.`, ok: "Close all", danger: true });
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
      header.prices[e.symbol] = { price: e.price, age_seconds: 0, fresh: true };
      renderTicker(header.prices);
    });
    on("SettingsChanged", (e) => { if (e.trading_stopped) toast("Broker settings changed — trading switched off"); });
    on("OrderFailed", (e) => toast(`Order failed: ${e.error}`, "error"));
    on("PositionClosed", (e) => toast(`${e.symbol} closed (${e.reason}) ${signed(e.pnl, 4)}`, e.pnl >= 0 ? "ok" : "error"));
  }

  window.TB = {
    $, esc, num, price, money, signed, pnlClass, time, dateTime, ago, duration, side, status, eventBadge,
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
