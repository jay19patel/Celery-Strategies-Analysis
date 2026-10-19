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

const [pagePath, payloadPath] = process.argv.slice(2);
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
global.location = { href: "http://localhost/", search: "" };
global.history = { replaceState() {}, pushState() {} };
global.lucide = { createIcons() {} };

// Everything TB exposes, enough of it to run. Unknown members return a no-op rather than throwing,
// so a missing helper here is never mistaken for a bug in the page.
const TB = {
  $: stub, esc: String, num: (v, d = 2) => Number(v || 0).toFixed(d),
  price: String, money: String, pct: String, compact: String, count: String, ms: String,
  time: String, dateTime: String, ago: String, duration: String, sgn: String,
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
