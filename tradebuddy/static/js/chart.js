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

  // A `format` callback is code the caller wrote, not data a viewer typed, and several of the
  // app's formatters return markup on purpose — TB.count renders 8,640,217 as a compact "8.6M"
  // wrapped in a span carrying the exact figure as a tooltip. Escaping that put the tags on the
  // screen. So formatter output is inserted as markup where the surround is HTML, and flattened to
  // text where it is SVG, because an SVG <text> element cannot hold a span and would render empty.
  // Names, hints and anything else that came from data stay escaped.
  const flat = (v) => esc(String(v).replace(/<[^>]*>/g, ""));

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
      `<text x="${padL - 5}" y="${(Y(t) + 3.5).toFixed(1)}" text-anchor="end" font-size="9.5" fill="${AXIS}" font-family="ui-monospace,monospace">${flat(format(t))}</text>`,
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
        return `<span><span style="color:${s.color}">■</span> ${esc(s.name)} <span class="font-mono">${format(last.y)}</span></span>`;
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
        <div class="text-[11px] font-mono r" style="flex:0 0 3.5rem;text-align:right">${format(r.value)}</div>
      </div>`).join("")}</div>`;
  }

  /**
   * Candles, with the levels that matter drawn on them and room on the right for where price
   * could go next.
   *
   * bars:  [{time, open, high, low, close}]
   * opts:  { levels: [{price, label, color, dashed}], marks: [{bar_time, direction, kind}],
   *          predictions: [{bar_time, stance, price, settled, correct}],
   *          projection: {sigma_pct, hours}, height, width }
   *
   * The projection is the point of this chart rather than the candles: a cone of one and two
   * standard deviations over the next horizon, drawn to the same scale as the bars behind it. It
   * turns "expected move 1.7%" from a number into a distance you can see against the last swing.
   */
  function candles(bars, opts = {}) {
    const { height = 260, width = 720, levels = [], marks = [], projection = null, format = (v) => v.toLocaleString(undefined, { maximumFractionDigits: 0 }) } = opts;
    const rows = (bars || []).filter((b) => b && isFinite(b.close));
    if (rows.length < 2) return empty(height, "Not enough bars stored yet — the engine keeps one every 15 minutes.");

    const padL = 4, padR = 54, padT = 8, padB = 16;
    // The cone needs horizontal room: give it a quarter of the plot, which is also roughly how
    // much of the recent past stays readable beside it.
    const future = projection ? 0.25 : 0;
    const plotW = width - padL - padR;
    const pastW = plotW * (1 - future);

    const last = rows[rows.length - 1];
    let lo = Math.min(...rows.map((b) => b.low));
    let hi = Math.max(...rows.map((b) => b.high));
    for (const l of levels) if (isFinite(l.price)) { lo = Math.min(lo, l.price); hi = Math.max(hi, l.price); }
    if (projection?.sigma_pct) {
      lo = Math.min(lo, last.close * (1 - 2.2 * projection.sigma_pct / 100));
      hi = Math.max(hi, last.close * (1 + 2.2 * projection.sigma_pct / 100));
    }
    const span = (hi - lo) || 1;
    lo -= span * 0.04; hi += span * 0.04;

    const X = (i) => padL + (i / Math.max(1, rows.length - 1)) * pastW;
    const Y = (p) => padT + (1 - (p - lo) / (hi - lo)) * (height - padT - padB);
    const w = Math.max(1, Math.min(7, pastW / rows.length * 0.68));

    const body = rows.map((b, i) => {
      const up = b.close >= b.open;
      const col = up ? "#00b852" : "#eb5757";
      const x = X(i);
      const yO = Y(b.open), yC = Y(b.close);
      const top = Math.min(yO, yC), h = Math.max(0.8, Math.abs(yC - yO));
      return `<line x1="${x.toFixed(1)}" y1="${Y(b.high).toFixed(1)}" x2="${x.toFixed(1)}" y2="${Y(b.low).toFixed(1)}" stroke="${col}" stroke-width="1"/>`
        + `<rect x="${(x - w / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" fill="${col}"/>`;
    }).join("");

    const levelLines = levels.filter((l) => isFinite(l.price)).map((l) => {
      const y = Y(l.price);
      return `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${(width - padR).toFixed(1)}" y2="${y.toFixed(1)}"
          stroke="${l.color || AXIS}" stroke-width="1" ${l.dashed === false ? "" : 'stroke-dasharray="4 3"'}/>
        <text x="${width - padR + 3}" y="${(y + 3.5).toFixed(1)}" font-size="9.5" fill="${l.color || AXIS}"
          font-family="ui-monospace,monospace">${esc(l.label || format(l.price))}</text>`;
    }).join("");

    // A break is marked on the bar it happened, pointing the way it broke.
    const byTime = new Map(rows.map((b, i) => [b.time, i]));
    const markers = marks.filter((m) => byTime.has(m.bar_time)).map((m) => {
      const i = byTime.get(m.bar_time), b = rows[i];
      const up = m.direction === "up";
      const y = up ? Y(b.high) - 6 : Y(b.low) + 6;
      const col = m.kind === "choch" ? "#d97706" : (up ? "#00b852" : "#eb5757");
      const pts = up ? `${X(i)},${y - 5} ${X(i) - 4},${y + 2} ${X(i) + 4},${y + 2}` : `${X(i)},${y + 5} ${X(i) - 4},${y - 2} ${X(i) + 4},${y - 2}`;
      return `<polygon points="${pts}" fill="${col}"><title>${esc(m.kind)} ${esc(m.direction)}</title></polygon>`;
    }).join("");

    // What was said, drawn where it was said, faint enough to stay behind the price. A reading is
    // cheap to make and easy to forget; seeing a fortnight of them against what the market then did
    // is the only way to know whether to believe the one at the right-hand edge. Still open: hollow
    // and grey, because it has not earned a colour yet.
    const claims = (opts.predictions || []).filter((p) => byTime.has(p.bar_time)).map((p) => {
      const i = byTime.get(p.bar_time), b = rows[i];
      const bull = p.stance === "bullish";
      const y = bull ? Y(b.low) + 9 : Y(b.high) - 9;
      const col = !p.settled ? "#94a3b8" : p.correct ? "#00b852" : "#eb5757";
      const open = !p.settled;
      const d = bull
        ? `M ${X(i) - 3.5} ${y + 3} L ${X(i)} ${y - 2} L ${X(i) + 3.5} ${y + 3}`
        : `M ${X(i) - 3.5} ${y - 3} L ${X(i)} ${y + 2} L ${X(i) + 3.5} ${y - 3}`;
      const title = `${esc(p.label || (bull ? "leaned up" : "leaned down"))} — `
        + (p.settled ? (p.correct ? "held" : "did not hold") : "still open");
      return `<path d="${d}" fill="none" stroke="${col}" stroke-width="1.4" stroke-linecap="round"
        opacity="${open ? 0.45 : 0.55}"><title>${title}</title></path>`;
    }).join("");

    let cone = "";
    if (projection?.sigma_pct) {
      const x0 = X(rows.length - 1), x1 = width - padR;
      const p = last.close, s = projection.sigma_pct / 100;
      const wedge = (mult, fill) => `<path d="M ${x0.toFixed(1)} ${Y(p).toFixed(1)} L ${x1.toFixed(1)} ${Y(p * (1 + mult * s)).toFixed(1)}
        L ${x1.toFixed(1)} ${Y(p * (1 - mult * s)).toFixed(1)} Z" fill="${fill}"/>`;
      cone = wedge(2, "rgba(30,136,229,0.09)") + wedge(1, "rgba(30,136,229,0.17)")
        + `<line x1="${x0.toFixed(1)}" y1="${padT}" x2="${x0.toFixed(1)}" y2="${height - padB}" stroke="${GRID}" stroke-dasharray="2 3"/>`
        + `<text x="${x1}" y="${(Y(p * (1 + s)) - 3).toFixed(1)}" text-anchor="end" font-size="9" fill="${AXIS}">+1σ</text>`
        + `<text x="${x1}" y="${(Y(p * (1 - s)) + 9).toFixed(1)}" text-anchor="end" font-size="9" fill="${AXIS}">−1σ</text>`;
    }

    return `<svg viewBox="0 0 ${width} ${height}" class="w-full" style="height:${height}px" role="img"
      aria-label="${esc(opts.label || "price with levels")}">${cone}${levelLines}${claims}${body}${markers}</svg>`;
  }

  /**
   * Where price sits between two levels, as a bar with a marker. Answers "am I at the top of the
   * range or the bottom" faster than two numbers and a subtraction can.
   */
  function rangeBar(low, high, price, opts = {}) {
    if (!isFinite(low) || !isFinite(high) || !(high > low)) return `<div class="text-[11px] muted">No range yet.</div>`;
    const pos = Math.min(1, Math.max(0, (price - low) / (high - low)));
    const fmt = opts.format || ((v) => v.toLocaleString(undefined, { maximumFractionDigits: 0 }));
    return `<div>
      <div class="relative bg-gradient-to-r from-bull-bg via-slate-100 to-bear-bg rounded-sm" style="height:14px">
        <div class="absolute top-0 bottom-0" style="left:${(100 * pos).toFixed(1)}%;width:2px;background:#0f172a"></div>
      </div>
      <div class="flex justify-between text-[10.5px] muted font-mono mt-0.5">
        <span>${fmt(low)}</span><span class="text-slate-700">${(100 * pos).toFixed(0)}% up</span><span>${fmt(high)}</span>
      </div></div>`;
  }

  /**
   * A probability against the baseline it has to beat. Never one without the other: a hit rate on
   * its own cannot tell an edge from a market that simply went up all year.
   */
  function probBar(value, baseline, opts = {}) {
    const { sample = null, minSample = 0, height = 18 } = opts;
    if (value == null) {
      return `<div class="text-[11px] muted">${esc(sample != null && minSample
        ? `${sample.toLocaleString()} of ${minSample.toLocaleString()} outcomes needed`
        : "not enough history")}</div>`;
    }
    const edge = baseline == null ? null : value - baseline;
    const col = edge == null ? "#1e88e5" : edge > 0.01 ? "#00b852" : edge < -0.01 ? "#eb5757" : "#94a3b8";
    return `<div>
      <div class="relative bg-slate-100 rounded-sm overflow-hidden" style="height:${height}px">
        <div style="width:${(100 * value).toFixed(1)}%;height:100%;background:${col};opacity:.85"></div>
        ${baseline == null ? "" : `<div class="absolute top-0 bottom-0" style="left:${(100 * baseline).toFixed(1)}%;width:2px;background:#0f172a"
          title="baseline ${(100 * baseline).toFixed(1)}%"></div>`}
        <div class="absolute inset-0 flex items-center justify-between px-1.5 text-[10.5px] font-mono">
          <span class="text-slate-800 font-semibold">${(100 * value).toFixed(1)}%</span>
          ${baseline == null ? "" : `<span class="text-slate-600">base ${(100 * baseline).toFixed(1)}%</span>`}
        </div>
      </div>
      ${edge == null ? "" : `<div class="text-[10.5px] mt-0.5 ${edge > 0.01 ? "up" : edge < -0.01 ? "down" : "muted"}">
        ${edge >= 0 ? "+" : ""}${(100 * edge).toFixed(1)} pts vs baseline${sample ? ` · ${sample.toLocaleString()} bars` : ""}</div>`}
    </div>`;
  }

  window.TBC = { timeLine, hbars, candles, rangeBar, probBar, colorFor, niceTicks, ROLE_COLOR };
})();
