// Small SVG chart kit. No library: the pages already hand-roll their charts (analysis.js), a CDN
// is one more third party on a page that shows positions, and what is needed here is a line and a
// bar, not a plotting framework.
//
// Everything returns an SVG string, so a caller does `el.innerHTML = TBC.timeLine(...)`.
// Nothing here reads the DOM or the network.
(() => {
  const PALETTE = ["#1e88e5", "#7c3aed", "#0f766e", "#eb5757", "#d97706", "#0891b2"];
  // One colour per process, stable across the three charts, so a line can be followed between them.
  const ROLE_COLOR = { all: "#1e88e5", engine: "#1e88e5", feed: "#0f766e", analyst: "#7c3aed", web: "#d97706", worker: "#0891b2" };
  const GRID = "#e2e8f0";
  const AXIS = "#94a3b8";

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const colorFor = (name, i) => ROLE_COLOR[name] || PALETTE[i % PALETTE.length];

  // "Nothing to draw" is a sentence, not an empty box: a chart that renders blank looks broken,
  // and the usual reason here is simply that the system has not been running long enough yet.
  const empty = (height, text) =>
    `<div class="flex items-center justify-center text-[11px] muted text-center px-3" style="height:${height}px">${esc(text)}</div>`;

  // Round a span out to readable ticks instead of labelling the raw min and max.
  function niceTicks(lo, hi, count = 4) {
    if (!(hi > lo)) return [lo];
    const raw = (hi - lo) / count;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].find((m) => m * mag >= raw) * mag;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + step / 1000; v += step) out.push(Number(v.toFixed(10)));
    return out.length ? out : [lo, hi];
  }

  const shortTime = (ts) =>
    new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

  /**
   * Several series against time on one scale.
   *
   * series: [{ name, points: [{x: epochSeconds, y: number}], color? }]
   * opts:   { height, width, format, gapSeconds, label, hideLegend }
   *
   * `gapSeconds` matters: a line drawn straight across a window with no samples shows a smooth
   * trend through time the process was not running. Past that spacing the line is broken instead.
   */
  function timeLine(series, opts = {}) {
    const { height = 150, width = 520, format = (v) => String(Math.round(v)), gapSeconds = 0, label = "", hideLegend = false } = opts;
    const live = (series || [])
      .map((s, i) => ({ ...s, color: s.color || colorFor(s.name, i), points: (s.points || []).filter((p) => p && p.y != null && isFinite(p.y)) }))
      .filter((s) => s.points.length);
    if (!live.length) return empty(height, opts.emptyText || "Nothing recorded yet.");

    const all = live.flatMap((s) => s.points);
    const xs = all.map((p) => p.x), ys = all.map((p) => p.y);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    let lo = Math.min(...ys), hi = Math.max(...ys);
    if (hi === lo) { hi = lo + Math.abs(lo || 1) * 0.1; lo -= Math.abs(lo || 1) * 0.1; }
    // A load chart that does not include zero makes 2% CPU look like a spike.
    if (lo > 0 && lo < (hi - lo)) lo = 0;

    const padL = 42, padR = 10, padT = 10, padB = 18;
    const X = (x) => padL + ((x - x0) / (x1 - x0 || 1)) * (width - padL - padR);
    const Y = (y) => height - padB - ((y - lo) / (hi - lo || 1)) * (height - padT - padB);

    const ticks = niceTicks(lo, hi).filter((t) => t >= lo && t <= hi);
    const grid = ticks.map((t) =>
      `<line x1="${padL}" y1="${Y(t).toFixed(1)}" x2="${width - padR}" y2="${Y(t).toFixed(1)}" stroke="${GRID}" stroke-width="1"/>` +
      `<text x="${padL - 5}" y="${(Y(t) + 3.5).toFixed(1)}" text-anchor="end" font-size="9.5" fill="${AXIS}" font-family="ui-monospace,monospace">${esc(format(t))}</text>`,
    ).join("");

    const paths = live.map((s) => {
      const sorted = [...s.points].sort((a, b) => a.x - b.x);
      const runs = [[]];
      sorted.forEach((p, i) => {
        if (i && gapSeconds && p.x - sorted[i - 1].x > gapSeconds) runs.push([]);
        runs[runs.length - 1].push(p);
      });
      const lines = runs.filter((r) => r.length > 1)
        .map((r) => `<polyline fill="none" stroke="${s.color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"
          vector-effect="non-scaling-stroke" points="${r.map((p) => `${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join(" ")}"/>`).join("");
      // A single sample in a run would be invisible as a line; show it as the point it is.
      const dots = runs.filter((r) => r.length === 1)
        .map((r) => `<circle cx="${X(r[0].x).toFixed(1)}" cy="${Y(r[0].y).toFixed(1)}" r="2" fill="${s.color}"/>`).join("");
      const last = sorted[sorted.length - 1];
      return lines + dots + `<circle cx="${X(last.x).toFixed(1)}" cy="${Y(last.y).toFixed(1)}" r="2.6" fill="${s.color}"/>`;
    }).join("");

    const axis = `<line x1="${padL}" y1="${height - padB}" x2="${width - padR}" y2="${height - padB}" stroke="${GRID}"/>
      <text x="${padL}" y="${height - 5}" font-size="9.5" fill="${AXIS}" font-family="ui-monospace,monospace">${esc(shortTime(x0))}</text>
      <text x="${width - padR}" y="${height - 5}" text-anchor="end" font-size="9.5" fill="${AXIS}" font-family="ui-monospace,monospace">${esc(shortTime(x1))}</text>`;

    const legend = hideLegend ? "" : `<div class="flex flex-wrap gap-x-3 gap-y-0.5 text-[10.5px] muted mt-0.5">${
      live.map((s) => {
        const last = [...s.points].sort((a, b) => a.x - b.x).pop();
        return `<span><span style="color:${s.color}">■</span> ${esc(s.name)} <span class="font-mono">${esc(format(last.y))}</span></span>`;
      }).join("")}</div>`;

    return `<svg viewBox="0 0 ${width} ${height}" class="w-full" style="height:${height}px" role="img"
        aria-label="${esc(label || "time series")}">${grid}${axis}${paths}</svg>${legend}`;
  }

  /**
   * Horizontal bars, for comparing counts at a glance where a column of numbers does not.
   * items: [{ name, value, hint?, color? }]
   */
  function hbars(items, opts = {}) {
    const { format = (v) => String(v), height = 10, emptyText = "Nothing yet." } = opts;
    const rows = (items || []).filter((r) => r && isFinite(r.value));
    if (!rows.length) return `<div class="text-[11px] muted">${esc(emptyText)}</div>`;
    const max = Math.max(...rows.map((r) => Math.abs(r.value)), 1);
    return `<div class="space-y-1">${rows.map((r, i) => `
      <div class="flex items-center gap-2">
        <div class="text-[11px] truncate" style="flex:0 0 40%" title="${esc(r.hint || r.name)}">${esc(r.name)}</div>
        <div class="flex-1 bg-slate-100 rounded-sm overflow-hidden" style="height:${height}px">
          <div style="width:${(100 * Math.abs(r.value) / max).toFixed(1)}%;height:100%;background:${r.color || colorFor(r.name, i)}"></div>
        </div>
        <div class="text-[11px] font-mono r" style="flex:0 0 3.5rem;text-align:right">${esc(format(r.value))}</div>
      </div>`).join("")}</div>`;
  }

  window.TBC = { timeLine, hbars, colorFor, niceTicks, ROLE_COLOR };
})();
