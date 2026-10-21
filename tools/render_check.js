// Run a dashboard page's own JavaScript against real API payloads and report what it drew.
//
//   python -m tools.render_check            # captures payloads, then runs this
//   node tools/render_check.js <page.html> <payloads.json>
//
// `node --check` only parses. It passed happily while the Market page was throwing on every load —
// once because a local named `history` shadowed window.history, once because an edit deleted
// renderSummary and nothing called it until paint. Both reached the browser; neither reached the
// test suite. This executes the page the way a browser would, against the payloads the real API
// returns, and prints how many bytes each panel received. A panel at 0 bytes is a blank card.
//
// The DOM here is a stub: every element exists and remembers what was written to it. That is on
// purpose — a missing element must not be able to hide a real throw behind a quiet no-op.

const fs = require("node:fs");
const path = require("node:path");

const [pagePath, payloadPath, dumpId, snapshotPath] = process.argv.slice(2);
if (!pagePath || !payloadPath) {
  console.error("usage: node tools/render_check.js <page.html> <payloads.json>");
  process.exit(2);
}
const root = path.resolve(__dirname, "..");
const payloads = JSON.parse(fs.readFileSync(payloadPath, "utf8"));
const page = fs.readFileSync(pagePath, "utf8");

// The Tailwind config block configures a CDN that is not here; skip it, it draws nothing.
const scripts = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map((m) => m[1])
  .filter((s) => !/tailwind\.config/.test(s));

const written = {};
const stub = (id) => ({
  get innerHTML() { return written[id] || ""; },
  set innerHTML(v) { written[id] = v; },
  textContent: "", value: "48", scrollTop: 0, offsetTop: 0, clientHeight: 0, dataset: {},
  classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
  querySelectorAll: () => [], querySelector: () => null, closest: () => null,
  addEventListener() {}, insertAdjacentHTML() {},
});

global.document = {
  getElementById: stub, querySelectorAll: () => [], querySelector: () => null,
  createElement: () => stub("created"), addEventListener() {},
};
global.window = global;
// `window.addEventListener` as well as `document.`'s: a page that binds a document-level drag or
// key handler at load — which is the only correct place to bind one — would otherwise throw here
// and take every panel down with it.
global.addEventListener = () => {};
global.removeEventListener = () => {};
global.location = { href: "http://localhost/", search: "" };
global.history = { replaceState() {}, pushState() {} };
global.lucide = { createIcons() {} };

// Everything TB exposes, enough of it to run. Unknown members return a no-op rather than throwing,
// so a missing helper here is never mistaken for a bug in the page.
// The formatters are the REAL ones, lifted from app.js. They used to be `String`, which was fine
// for counting bytes and actively misleading once this harness started feeding `tools/snapshot`:
// a screenshot showed `$75368951.6738` and `$undefined` in the KPI row, and neither was a bug in
// the page — `compact` simply was not `compact`. A harness that invents defects costs more than
// no harness at all.
const _num = (v, d = 2) => (v === null || v === undefined || v === "" || Number.isNaN(Number(v))
  ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: d }));
const _compact = (v) => (v === null || v === undefined || Number.isNaN(Number(v))
  ? "—" : Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(v));
const TB = {
  $: stub, esc: String, num: _num,
  price: (v) => (v === null || v === undefined ? "—" : _num(v, Math.abs(v) >= 100 ? 2 : Math.abs(v) >= 1 ? 4 : 6)),
  money: (v, d = 2) => (v === null || v === undefined ? "—" : `${v < 0 ? "-" : ""}$${_num(Math.abs(v), d)}`),
  pct: (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`),
  compact: _compact, count: _compact,
  ms: (v) => (v === null || v === undefined ? "—" : v >= 1000 ? `${_num(v / 1000, 2)} s` : `${_num(v, v >= 100 ? 0 : 1)} ms`),
  time: (v) => new Date((v || 0) * 1000).toISOString().slice(11, 16),
  dateTime: (v) => new Date((v || 0) * 1000).toISOString().slice(0, 16).replace("T", " "),
  ago: (v) => { const s = Date.now() / 1000 - (v || 0);
    return s < 60 ? `${s | 0}s ago` : s < 3600 ? `${(s / 60) | 0}m ago` : `${(s / 3600) | 0}h ago`; },
  duration: (v) => (v === null || v === undefined ? "—" : `${(v / 60) | 0}m`), sgn: (v, d = 2) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`),
  rows() {}, icons() {}, every() {}, on() {}, toast() {}, post: async () => ({}),
  loadHeader: async () => {}, header: payloads["/api/header"],
  eventBadge: () => "", aiBadge: () => "", aiStatus: () => "",
  get: async (url) => {
    if (!(url in payloads)) throw new Error(`no captured payload for ${url}`);
    return payloads[url];
  },
};
global.TB = new Proxy(TB, { get: (t, k) => (k in t ? t[k] : () => "") });

require(path.join(root, "tradebuddy/static/js/chart.js"));
// The price chart is a real canvas library and cannot run against a stub DOM. Standing in for it
// records what the page ASKED it to draw, which is the part this harness can check: a tape that
// silently stops being handed its zones or its levels shows up here as an empty payload.
global.ResizeObserver = class { observe() {} disconnect() {} };
global.TBTape = {
  available: false, esc: String, fmt: String, KIND_COLOR: {},
  mount(el) {
    const drawn = { bars: [], zones: [], levels: [], marks: [], sub: [] };
    el.innerHTML = "[chart]";
    return {
      ok: true, update(next) { Object.assign(drawn, next); el.innerHTML = JSON.stringify(
        Object.fromEntries(Object.entries(drawn).map(([k, v]) => [k, Array.isArray(v) ? v.length : v ? 1 : 0]))); },
      fit() {}, zoom() {}, nudge() {}, destroy() {}, drawn,
    };
  },
};
// analysis.js assigns window.TBA at the end; eval keeps it in this global.
eval(fs.readFileSync(path.join(root, "tradebuddy/static/js/analysis.js"), "utf8"));

let failures = 0;
for (const [i, src] of scripts.entries()) {
  try {
    eval(src);
  } catch (e) {
    failures++;
    console.error(`script ${i} threw: ${e.message}`);
    console.error((e.stack || "").split("\n").slice(1, 4).join("\n"));
  }
}
process.on("unhandledRejection", (e) => {
  failures++;
  console.error(`async rejection: ${e && e.message}`);
});

// The loaders are async; give them a tick to resolve before reporting.
setTimeout(() => {
  if (snapshotPath) {
    fs.writeFileSync(snapshotPath, JSON.stringify(written));
    process.exit(failures ? 1 : 0);
  }
  if (dumpId) {
    process.stdout.write(written[dumpId] == null ? `no panel named ${dumpId}\n` : written[dumpId] + "\n");
    process.exit(failures ? 1 : 0);
  }
  const drawn = Object.entries(written).filter(([, v]) => v && v.length);
  const empty = Object.entries(written).filter(([, v]) => !v || !v.length);
  console.log(failures ? `\n${failures} failure(s)` : "\nno script threw");
  console.log(`${drawn.length} panel(s) drawn, ${empty.length} left empty`);
  for (const [id, html] of drawn.sort((a, b) => b[1].length - a[1].length)) {
    console.log(`  ${id.padEnd(18)} ${String(html.length).padStart(7)} bytes`);
  }
  for (const [id] of empty) console.log(`  ${id.padEnd(18)}       0 bytes  <- empty`);
  process.exit(failures ? 1 : 0);
}, 800);
