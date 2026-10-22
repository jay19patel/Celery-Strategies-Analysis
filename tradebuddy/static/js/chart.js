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

  // How much time the chart covers, said once in the middle of the axis. Two timestamps alone
  // leave the reader to subtract them, and they usually do not.
  const spanLabel = (seconds) => {
    const h = seconds / 3600;
    if (h < 1) return `${Math.round(seconds / 60)} min`;
    if (h < 48) return `${Math.round(h)} hours`;
    return `${Math.round(h / 24)} days`;
  };

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

  // HH:MM alone made a 24-hour window read backwards — "22:30" on the left and "22:15" on the
  // right are a day apart, not fifteen minutes. Past a few hours the label carries the date.
  const axisTime = (ts, spanSeconds) => {
    const d = new Date(ts * 1000);
    const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
    if (spanSeconds < 6 * 3600) return hm;
    const day = d.toLocaleDateString([], { day: "2-digit", month: "short" });
    return spanSeconds > 6 * 86400 ? day : `${day} ${hm}`;
  };

  /**
   * Several series against time on one scale.
   *
   * series: [{ name, points: [{x: epochSeconds, y: number}], color? }]
   * opts:   { height, width, format, gapSeconds, label, hideLegend, guides: [{at, label, color}] }
   *
   * `gapSeconds` matters: a line drawn straight across a window with no samples shows a smooth
   * trend through time the process was not running. Past that spacing the line is broken instead.
   */
  function timeLine(series, opts = {}) {
    const { height = 150, width = 520, format = (v) => String(Math.round(v)), gapSeconds = 0, label = "", hideLegend = false, guides = [] } = opts;
    const live = (series || [])
      .map((s, i) => ({ ...s, color: s.color || colorFor(s.name, i), points: (s.points || []).filter((p) => p && p.y != null && isFinite(p.y)) }))
      .filter((s) => s.points.length);
    if (!live.length) return empty(height, opts.emptyText || "Nothing recorded yet.");

    const all = live.flatMap((s) => s.points);
    const xs = all.map((p) => p.x), ys = all.map((p) => p.y);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    let lo = Math.min(...ys), hi = Math.max(...ys);
    // A caption saying "30 and 70 marked" on a chart scaled 40–60 marks nothing. Pull the range
    // out to include every guide so the line it promises is actually on it.
    for (const g of guides) if (isFinite(g.at)) { lo = Math.min(lo, g.at); hi = Math.max(hi, g.at); }
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

    const guideLines = guides.filter((g) => isFinite(g.at)).map((g) => {
      const y = Y(g.at);
      return `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${width - padR}" y2="${y.toFixed(1)}"
          stroke="${g.color || "#cbd5e1"}" stroke-width="1" stroke-dasharray="3 3"/>
        <text x="${width - padR - 2}" y="${(y - 2).toFixed(1)}" text-anchor="end" font-size="8.5"
          fill="${g.color || AXIS}">${esc(g.label ?? g.at)}</text>`;
    }).join("");

    const axis = `<line x1="${padL}" y1="${height - padB}" x2="${width - padR}" y2="${height - padB}" stroke="${GRID}"/>
      <text x="${padL}" y="${height - 5}" font-size="9.5" fill="${AXIS}" font-family="ui-monospace,monospace">${esc(axisTime(x0, x1 - x0))}</text>
      <text x="${(padL + width - padR) / 2}" y="${height - 5}" text-anchor="middle" font-size="9" fill="${AXIS}">${esc(spanLabel(x1 - x0))}</text>
      <text x="${width - padR}" y="${height - 5}" text-anchor="end" font-size="9.5" fill="${AXIS}" font-family="ui-monospace,monospace">${esc(axisTime(x1, x1 - x0))}</text>`;

    const legend = hideLegend ? "" : `<div class="flex flex-wrap gap-x-3 gap-y-0.5 text-[10.5px] muted mt-0.5">${
      live.map((s) => {
        const last = [...s.points].sort((a, b) => a.x - b.x).pop();
        return `<span><span style="color:${s.color}">■</span> ${esc(s.name)} <span class="font-mono">${format(last.y)}</span></span>`;
      }).join("")}</div>`;

    return `<svg viewBox="0 0 ${width} ${height}" class="w-full" style="height:${height}px" role="img"
        aria-label="${esc(label || "time series")}">${grid}${guideLines}${axis}${paths}</svg>${legend}`;
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

  /**
   * Volume by price — the one chart here whose vertical axis is price and whose bars run sideways.
   *
   * levels: [{price, volume, share, in_value, poc}]
   * opts:   { price, vwap, bands, height, format }
   *
   * Drawn this way because that is the question it answers: not "when did size trade" but "at what
   * price". The point of control and the edges of the value area are levels a trade goes against,
   * so they are labelled rather than left to be read off a bar.
   */
  function volumeProfile(levels, opts = {}) {
    const rows = (levels || []).filter((l) => l && isFinite(l.volume));
    const { height = 260, width = 300, price = null, vwap = null, format = (v) => Math.round(v).toLocaleString() } = opts;
    if (rows.length < 4) return empty(height, "Not enough bars to build a profile yet.");
    const max = Math.max(...rows.map((l) => l.volume), 1);
    const lo = Math.min(...rows.map((l) => l.price));
    const hi = Math.max(...rows.map((l) => l.price));
    const padR = 62, plot = width - padR;
    const Y = (p) => height - 6 - ((p - lo) / (hi - lo || 1)) * (height - 12);
    const bandH = Math.max(1.5, (height - 12) / rows.length - 1);

    const bars = rows.map((l) => {
      const w = (l.volume / max) * plot;
      const fill = l.poc ? "#d97706" : l.in_value ? "#1e88e5" : "#cbd5e1";
      return `<rect x="${(plot - w).toFixed(1)}" y="${(Y(l.price) - bandH / 2).toFixed(1)}" width="${w.toFixed(1)}"
        height="${bandH.toFixed(1)}" fill="${fill}" opacity="${l.poc ? 0.95 : l.in_value ? 0.65 : 0.5}">
        <title>${esc(format(l.price))} — ${(100 * (l.share || 0)).toFixed(1)}% of the window's volume</title></rect>`;
    }).join("");

    const mark = (p, colour, label, dashed) => (p == null || p < lo || p > hi) ? "" :
      `<line x1="0" y1="${Y(p).toFixed(1)}" x2="${plot}" y2="${Y(p).toFixed(1)}" stroke="${colour}" stroke-width="1.4"
         ${dashed ? 'stroke-dasharray="4 3"' : ""}/>
       <text x="${plot + 4}" y="${(Y(p) + 3.5).toFixed(1)}" font-size="9.5" fill="${colour}"
         font-family="ui-monospace,monospace">${esc(label)}</text>`;
    const poc = rows.find((l) => l.poc);
    return `<svg viewBox="0 0 ${width} ${height}" class="w-full" style="height:${height}px" role="img"
        aria-label="Volume traded at each price">${bars}
      ${poc ? mark(poc.price, "#d97706", `POC ${format(poc.price)}`, false) : ""}
      ${mark(vwap, "#7c3aed", `VWAP ${format(vwap)}`, true)}
      ${mark(price, "#0f172a", `now ${format(price)}`, false)}
    </svg>`;
  }


  /**
   * The tape: one chart that answers the whole question.
   *
   * This replaces eight tabs, and the reason it can is that seven of them were lists of PRICES —
   * pools, zones, reference levels, value-area edges, option walls. A price belongs on a price
   * axis. Reading it off a table meant doing in your head the one thing a chart is for.
   *
   * Everything here shares ONE vertical scale: the candles, the zone rectangles, the level lines
   * and the volume-by-price histogram in the right-hand gutter. That is the whole trick. Two
   * charts side by side with their own axes cannot be compared by eye, however neatly they are
   * aligned; one axis means the POC is drawn at the same height as the price it refers to and the
   * comparison is free.
   *
   *   bars     [{time, open, high, low, close, volume}]
   *   zones    [{low, high, kind, direction, state, created_at}] drawn from where they formed
   *   levels   [{price, label, kind, confluence}] horizontal, labelled in the right margin
   *   marks    [{bar_time, direction, kind}] structure breaks
   *   sweeps   [{bar_time, direction}] stop raids, drawn as a hook through the level
   *   profile  {levels:[{price, share, in_value, poc}]} the right gutter
   *   sub      {mode:"volume"|"delta", bars:[{time, value, up}]} the pane underneath
   *
   * Layers are passed in already filtered — the caller owns what is visible, this owns how it is
   * drawn.
   */
  // `tape()` used to live here: a hand-rolled candle chart with its own wheel, drag and crosshair
  // handlers. It is now `static/js/tape.js` on TradingView's Lightweight Charts — a price chart
  // needs zoom, pan, a snapping crosshair and a canvas renderer, and each of those is a week of
  // work the library has already done. Everything else on the dashboard stays hand-rolled SVG,
  // because a bar chart does not need 160 KB to draw six bars.


  /**
   * Open interest and volume by strike: puts left, calls right, spot in the middle.
   *
   * The option chain is a table of a hundred numbers and the question anyone actually has of it is
   * shaped like a picture — WHERE is the size, and is it calls or puts? Mirrored bars answer that
   * in one look: the longest bar on the right is the call wall, the longest on the left is the put
   * wall, and a lopsided chart is a positioned market.
   *
   * Open interest is the solid bar — contracts that exist and must eventually be closed or settled.
   * Volume is the darker inner bar: what changed hands TODAY. A strike with big OI and no volume is
   * an old position sitting there; big volume and little OI is something being opened right now,
   * and those are very different pieces of news.
   */
  function strikeLadder(rows, opts = {}) {
    const live = (rows || []).filter((r) => r && isFinite(r.strike));
    if (live.length < 2) return empty(opts.height || 300, "No contracts near spot for this expiry.");
    const { spot = null, maxPain = null, height = 340, format = (v) => Math.round(v).toLocaleString() } = opts;
    const CALL = "#2a78d6", PUT = "#eb6834";

    const oiOf = (s) => (s && s.oi) || 0;
    const volOf = (s) => (s && s.volume) || 0;
    const peak = Math.max(...live.flatMap((r) => [oiOf(r.call), oiOf(r.put)]), 1e-9);

    // Nearest the money first is the wrong order for a ladder: price runs down the page, so the
    // strikes do too, highest at the top, the way every option chain is read.
    const sorted = [...live].sort((a, b) => b.strike - a.strike);
    const rowH = Math.max(9, Math.min(20, (height - 26) / sorted.length));
    const half = 46;  // % of width each side gets, leaving a gutter for the strike label

    const atm = spot == null ? null
      : sorted.reduce((best, r) => (Math.abs(r.strike - spot) < Math.abs(best.strike - spot) ? r : best), sorted[0]).strike;

    // The strike sits in a reserved gutter down the middle, not on top of the bars — with the
    // label overlaid the two longest bars always ran under it and the number was unreadable.
    const GUT = 13;  // % of width kept clear for the strike
    const side_ = (100 - GUT) / 2;
    const bar = (side, colour, right) => {
      const oi = oiOf(side), vol = volOf(side);
      if (!oi && !vol) return "";
      const w = (oi / peak) * side_;
      const vw = (Math.min(vol, oi) / peak) * side_;
      const edge = right ? `left:${50 + GUT / 2}%` : `right:${50 + GUT / 2}%`;
      return `<span style="position:absolute;${edge};top:14%;height:72%;width:${w.toFixed(2)}%;
          background:${colour};opacity:.28;border-radius:2px"></span>
        ${vw > 0.15 ? `<span style="position:absolute;${edge};top:30%;height:40%;width:${vw.toFixed(2)}%;
          background:${colour};opacity:.9;border-radius:1px"></span>` : ""}`;
    };

    const body = sorted.map((r) => {
      const isAtm = atm != null && r.strike === atm;
      const isPain = maxPain != null && Math.abs(r.strike - maxPain) < 1e-9;
      return `<div style="position:relative;height:${rowH}px" title="${format(r.strike)} — calls OI ${
          compactish(oiOf(r.call))} vol ${compactish(volOf(r.call))} · puts OI ${compactish(oiOf(r.put))} vol ${compactish(volOf(r.put))}">
        ${bar(r.put, PUT, false)}${bar(r.call, CALL, true)}
        <span style="position:absolute;left:50%;transform:translateX(-50%);top:0;height:100%;display:flex;align-items:center;
          justify-content:center;width:${GUT}%;font:600 9.5px ui-monospace,monospace;
          color:${isAtm ? "#0f172a" : "#64748b"};
          ${isAtm ? "background:#e2e8f0;border-radius:3px" : ""}">${format(r.strike)}${isPain ? " ◆" : ""}</span>
      </div>`;
    }).join("");

    return `<div>
      <div class="flex justify-between text-[10.5px] muted mb-1">
        <span style="color:${PUT}">◀ puts</span>
        <span>pale = open interest · solid = traded today${maxPain != null ? " · ◆ max pain" : ""}</span>
        <span style="color:${CALL}">calls ▶</span>
      </div>
      <div style="position:relative">${body}</div>
      <div class="flex justify-between text-[10px] muted mt-1" style="font-family:ui-monospace,monospace">
        <span>${compactish(peak)}</span><span>0</span><span>${compactish(peak)}</span>
      </div>
      <div class="text-[10.5px] muted mt-1">Widest bar is ${compactish(peak)} contracts of open interest${
        spot != null ? ` · spot ${format(spot)} is the highlighted strike` : ""}.</div>
    </div>`;
  }

  const compactish = (v) => (v == null ? "—" : Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v));


  /**
   * Time at price: how long the market spent at each price, as a sideways histogram.
   *
   * This replaces the TPO letter grid. The grid is the traditional form and it is genuinely hard
   * to read — twenty columns of letters, and the thing you are supposed to extract from them is
   * just "how many periods traded here". So print that directly. The letters are kept on hover
   * for anyone who wants to see which half hours made a price, which is the only question the
   * grid answered that a bar does not.
   *
   * A wide bar is a price the market accepted and kept coming back to. A single-period bar is a
   * price it rejected — those are the prices price moves THROUGH, which makes them targets rather
   * than supports.
   */
  function timeAtPrice(tpo, opts = {}) {
    const rows = (tpo?.rows || []).filter((r) => r && isFinite(r.price));
    if (rows.length < 3) return empty(opts.height || 300, "Not enough half-hours stored yet for a market profile.");
    const { height = 320, price = null, format = (v) => Math.round(v).toLocaleString() } = opts;
    const peak = Math.max(...rows.map((r) => r.count), 1);
    const sorted = [...rows].sort((a, b) => b.price - a.price);
    const rowH = Math.max(5, Math.min(18, (height - 18) / sorted.length));

    const band = (r) => {
      const w = (100 * r.count) / peak;
      const colour = r.poc ? "#d97706" : r.single ? "#cbd5e1" : r.in_value ? "#1e88e5" : "#94a3b8";
      const edge = [];
      if (tpo.ib_high != null && r.price <= tpo.ib_high && r.price >= tpo.ib_low) edge.push("initial balance");
      if (r.single) edge.push("single print — rejected, price moved straight through");
      if (r.poc) edge.push("point of control — most time spent here");
      if (tpo.poor_high && Math.abs(r.price - tpo.poor_high) < 1e-9) edge.push("poor high — no tail, price tends to come back for it");
      if (tpo.poor_low && Math.abs(r.price - tpo.poor_low) < 1e-9) edge.push("poor low — no tail");
      return `<div style="position:relative;height:${rowH}px;display:flex;align-items:center;gap:6px"
          title="${format(r.price)} — ${r.count} half-hour${r.count === 1 ? "" : "s"}${
            edge.length ? ` · ${edge.join(" · ")}` : ""}${r.letters ? ` · ${r.letters}` : ""}">
        <span style="width:4.6rem;text-align:right;font:9.5px ui-monospace,monospace;color:${
          r.poc ? "#0f172a" : "#94a3b8"};flex:none">${format(r.price)}</span>
        <span style="flex:1;position:relative;height:${Math.max(3, rowH - 2)}px">
          <span style="position:absolute;left:0;top:0;bottom:0;width:${w.toFixed(1)}%;background:${colour};
            opacity:${r.poc ? 0.95 : r.in_value ? 0.62 : 0.4};border-radius:1px"></span>
          ${tpo.ib_high != null && r.price <= tpo.ib_high && r.price >= tpo.ib_low
            ? `<span style="position:absolute;left:-3px;top:0;bottom:0;width:2px;background:#7c3aed;opacity:.75"></span>` : ""}
          ${price != null && Math.abs(r.price - price) < (sorted[0].price - sorted[sorted.length - 1].price) / sorted.length
            ? `<span style="position:absolute;left:0;right:0;top:50%;height:1.5px;background:#0f172a"></span>` : ""}
        </span></div>`;
    };

    return `<div>
      <div class="flex flex-wrap gap-x-3 gap-y-0.5 text-[10.5px] muted mb-1.5">
        <span><span style="display:inline-block;width:8px;height:8px;background:#d97706;border-radius:1px;vertical-align:middle"></span> most time here</span>
        <span><span style="display:inline-block;width:8px;height:8px;background:#1e88e5;opacity:.62;border-radius:1px;vertical-align:middle"></span> value area (70%)</span>
        <span><span style="display:inline-block;width:8px;height:8px;background:#cbd5e1;border-radius:1px;vertical-align:middle"></span> single print — rejected</span>
        <span><span style="display:inline-block;width:2px;height:9px;background:#7c3aed;vertical-align:middle"></span> first hour</span>
      </div>
      ${sorted.map(band).join("")}
      <p class="text-[10.5px] muted mt-1.5">Each bar is how many half-hours traded at that price. Long bars are prices the
      market kept coming back to; one-period bars are prices it rejected and moved straight through, which makes them
      better targets than supports. Hover a row for the half hours that made it.</p>
    </div>`;
  }

  /**
   * Where price has been accepted, day by day — the value area of each day as a band, with price
   * drawn over it and the points of control it never came back to.
   *
   * The question it answers is the one a list of naked POCs cannot: WHICH candles were made inside
   * value and which were made outside it. Price inside the band is a market agreeing with itself;
   * price leaving it is the day's move, and a naked POC left behind is unfinished business the
   * market has a habit of returning to.
   */
  function valueAreaMap(days, bars, naked, opts = {}) {
    const live = (days || []).filter((d) => d && isFinite(d.value_low) && isFinite(d.value_high));
    const rows = (bars || []).filter((b) => b && isFinite(b.close));
    if (!live.length || rows.length < 5) return empty(opts.height || 260, "Not enough stored days yet — run the backfill.");
    const { height = 260, width = 900, format = (v) => Math.round(v).toLocaleString() } = opts;
    const padL = 4, padR = 62, padT = 8, padB = 16;

    const t0 = Math.min(rows[0].time, ...live.map((d) => d.start));
    const t1 = Math.max(rows[rows.length - 1].time, ...live.map((d) => d.end));
    let lo = Math.min(...rows.map((b) => b.low), ...live.map((d) => d.value_low));
    let hi = Math.max(...rows.map((b) => b.high), ...live.map((d) => d.value_high));
    for (const n of naked || []) if (isFinite(n.price)) { lo = Math.min(lo, n.price); hi = Math.max(hi, n.price); }
    const pad = (hi - lo) * 0.04 || 1;
    lo -= pad; hi += pad;

    const X = (t) => padL + ((t - t0) / (t1 - t0 || 1)) * (width - padL - padR);
    const Y = (p) => padT + (1 - (p - lo) / (hi - lo)) * (height - padT - padB);

    // One band per day. Alternating tints so two days running do not read as one long band.
    const bands = live.map((d, i) => {
      const x0 = X(d.start), x1 = X(d.end);
      const colour = i % 2 ? "#1e88e5" : "#0891b2";
      return `<rect x="${x0.toFixed(1)}" y="${Y(d.value_high).toFixed(1)}" width="${Math.max(1, x1 - x0).toFixed(1)}"
          height="${Math.max(1, Y(d.value_low) - Y(d.value_high)).toFixed(1)}" fill="${colour}" opacity="0.13"/>
        <line x1="${x0.toFixed(1)}" y1="${Y(d.poc).toFixed(1)}" x2="${x1.toFixed(1)}" y2="${Y(d.poc).toFixed(1)}"
          stroke="#d97706" stroke-width="1.4" opacity="0.8"><title>${esc(d.label)} point of control ${format(d.poc)}</title></line>
        <text x="${(x0 + 3).toFixed(1)}" y="${(padT + 9).toFixed(1)}" font-size="9" fill="#94a3b8">${esc(d.label)}</text>`;
    }).join("");

    const line = rows.map((b, i) => `${i ? "L" : "M"} ${X(b.time).toFixed(1)} ${Y(b.close).toFixed(1)}`).join(" ");

    const pocs = (naked || []).filter((n) => isFinite(n.price)).map((n) => `
      <line x1="${padL}" y1="${Y(n.price).toFixed(1)}" x2="${(width - padR).toFixed(1)}" y2="${Y(n.price).toFixed(1)}"
        stroke="#0891b2" stroke-width="1.2" stroke-dasharray="2 4"/>
      <text x="${width - padR + 3}" y="${(Y(n.price) + 3.4).toFixed(1)}" font-size="9" fill="#0891b2"
        font-family="ui-monospace,monospace">${esc(format(n.price))}</text>
      <title>${esc(n.label || "")} point of control, never traded back to</title>`).join("");

    return `<div>
      <svg viewBox="0 0 ${width} ${height}" class="w-full" style="height:${height}px" role="img"
        aria-label="Value area by day with price drawn over it">
        ${bands}${pocs}
        <path d="${line}" fill="none" stroke="#0f172a" stroke-width="1.3"/>
      </svg>
      <div class="flex flex-wrap gap-x-3 gap-y-0.5 text-[10.5px] muted mt-1">
        <span><span style="display:inline-block;width:10px;height:8px;background:#1e88e5;opacity:.25;vertical-align:middle"></span> that day's value area — 70% of its volume</span>
        <span><span style="display:inline-block;width:10px;height:2px;background:#d97706;vertical-align:middle"></span> its point of control</span>
        <span><span style="display:inline-block;width:10px;height:0;border-top:2px dashed #0891b2;vertical-align:middle"></span> never traded back to</span>
      </div></div>`;
  }

  /**
   * The level ladder: what is above and what is below, as a shape rather than two tables.
   *
   * Distance is drawn to scale, so "price is sitting right under a wall of three agreeing levels"
   * is something you see instead of something you work out from a percentage column.
   */
  function ladder(above, below, price, opts = {}) {
    const { format = (v) => v.toLocaleString(undefined, { maximumFractionDigits: 0 }) } = opts;
    const all = [...(above || []), ...(below || [])];
    if (!all.length) return `<div class="text-[11px] muted">No levels within reach — the scan found nothing above or below.</div>`;
    const reach = Math.max(...all.map((l) => Math.abs(l.distance_atr)), 1);
    const KIND_COLOR = {
      pool: "#d97706", zone: "#7c3aed", poc: "#0891b2", value: "#0891b2",
      naked: "#0891b2", reference: "#64748b", wall: "#1e88e5",
    };
    const row = (l) => {
      const col = KIND_COLOR[l.kind] || "#94a3b8";
      const pct = (100 * Math.abs(l.distance_atr) / reach).toFixed(1);
      const strong = (l.confluence || 1) > 1;
      return `<div class="flex items-center gap-2 py-[3px]" title="${esc(l.evidence || "")}">
        <span class="font-mono text-[11px] tabular-nums" style="width:5.5rem;text-align:right;color:${col}">${esc(format(l.price))}</span>
        <span class="relative bg-slate-100 rounded-sm" style="flex:1;height:9px">
          <span class="absolute left-0 top-0 bottom-0 rounded-sm" style="width:${pct}%;background:${col};opacity:${strong ? 0.9 : 0.5}"></span>
        </span>
        <span class="text-[10.5px] muted truncate" style="width:9rem">${strong
          ? `<span style="color:${col}" title="${esc((l.agrees || []).join(', '))}">◆${l.confluence}</span> ` : ""}${esc(l.label || "")}</span>
        <span class="font-mono text-[10px] muted tabular-nums" style="width:3.2rem;text-align:right">${l.distance_atr.toFixed(1)}atr</span>
      </div>`;
    };
    return `<div>
      ${[...(above || [])].reverse().map(row).join("")}
      <div class="flex items-center gap-2 my-1">
        <span class="font-mono text-[11px] font-semibold tabular-nums" style="width:5.5rem;text-align:right">${esc(format(price))}</span>
        <span style="flex:1;height:1px;background:#0f172a"></span>
        <span class="text-[10.5px] font-semibold" style="width:9rem">price now</span>
        <span style="width:3.2rem"></span>
      </div>
      ${(below || []).map(row).join("")}
    </div>`;
  }


  /**
   * A row of outcomes, oldest left. Green worked, red did not, hollow still open.
   *
   * Replaces a table of twenty sweeps. A table makes you read twenty rows to answer "do these
   * usually work", which is the only question anyone asks of them — here the answer is the colour
   * of the row, and the individual events are still on hover.
   */
  function outcomeStrip(items, opts = {}) {
    const rows = items || [];
    if (!rows.length) return `<div class="text-[11px] muted">${esc(opts.empty || "Nothing in the window.")}</div>`;
    const { label = (r) => "", height = 22 } = opts;
    const cell = (r) => {
      const open = r.worked == null;
      const col = open ? "#cbd5e1" : r.worked ? "#00b852" : "#eb5757";
      return `<span title="${esc(label(r))}" style="flex:1 1 0;min-width:3px;height:${height}px;border-radius:2px;
        background:${open ? "transparent" : col};border:1.5px solid ${col};opacity:${open ? 0.6 : 0.9}"></span>`;
    };
    const done = rows.filter((r) => r.worked != null);
    const won = done.filter((r) => r.worked).length;
    return `<div>
      <div style="display:flex;gap:2px;align-items:stretch">${rows.map(cell).join("")}</div>
      <div class="flex justify-between text-[10.5px] muted mt-1">
        <span>oldest</span>
        <span>${done.length ? `${won} of ${done.length} worked` : "none resolved yet"}${
          rows.length > done.length ? ` · ${rows.length - done.length} still open` : ""}</span>
        <span>newest</span>
      </div></div>`;
  }

  /**
   * A lifecycle as one proportional bar: how many zones are fresh, tested, mitigated, dead.
   *
   * The states only ever move forward, so this bar is a picture of how worked-through the current
   * set of zones is — all-fresh after a move into new territory, all-mitigated in a range.
   */
  function stateBar(counts, opts = {}) {
    const order = opts.order || ["fresh", "tested", "mitigated", "invalidated"];
    const colour = { fresh: "#00b852", tested: "#1e88e5", mitigated: "#94a3b8", invalidated: "#e2e8f0" };
    const total = order.reduce((s, k) => s + (counts[k] || 0), 0);
    if (!total) return `<div class="text-[11px] muted">No zones found yet.</div>`;
    return `<div>
      <div style="display:flex;height:16px;border-radius:3px;overflow:hidden">${order.map((k) => {
        const n = counts[k] || 0;
        return n ? `<span title="${n} ${k}" style="width:${(100 * n / total).toFixed(1)}%;background:${colour[k]}"></span>` : "";
      }).join("")}</div>
      <div class="flex flex-wrap gap-x-3 gap-y-0.5 text-[10.5px] muted mt-1">${order.map((k) => {
        const n = counts[k] || 0;
        return n ? `<span><span style="display:inline-block;width:8px;height:8px;border-radius:1px;background:${colour[k]};vertical-align:middle"></span> ${n} ${esc(k)}</span>` : "";
      }).join("")}</div></div>`;
  }

  /**
   * Session ranges, one row per UTC day, each session a bar across a shared price axis.
   *
   * The question a table of session highs and lows cannot answer is the one worth asking: WHICH
   * session made the day's range. Drawn on one axis, that is immediate — a long New York bar under
   * a short Asia one is a day the move happened in the afternoon, and you can see a week of them
   * at a glance.
   */
  function sessionRanges(rows, opts = {}) {
    const live = (rows || []).filter((r) => r && isFinite(r.high) && isFinite(r.low) && r.high > r.low);
    if (!live.length) return `<div class="text-[11px] muted">No session data stored yet.</div>`;
    const { price = null, format = (v) => Math.round(v).toLocaleString() } = opts;
    const lo = Math.min(...live.map((r) => r.low), price ?? Infinity);
    const hi = Math.max(...live.map((r) => r.high), price ?? -Infinity);
    const span = (hi - lo) || 1;
    const COL = { asia: "#7c3aed", london: "#1e88e5", newyork: "#d97706" };
    const pos = (v) => (100 * (v - lo) / span);

    const byDay = {};
    for (const r of live) (byDay[r.day] ||= []).push(r);
    const days = Object.keys(byDay).sort().reverse();

    const body = days.map((day) => {
      const label = new Date(+day * 1000).toLocaleDateString([], { day: "2-digit", month: "short" });
      return `<div class="flex items-center gap-2 py-1 border-t border-slate-100 first:border-0">
        <span class="text-[10.5px] muted" style="width:3.6rem">${esc(label)}</span>
        <span class="relative" style="flex:1;height:34px">
          ${byDay[day].map((r, i) => `<span title="${esc(r.label)} ${format(r.low)}-${format(r.high)}${r.complete ? "" : " (still open)"}"
              style="position:absolute;left:${pos(r.low).toFixed(1)}%;width:${Math.max(0.6, pos(r.high) - pos(r.low)).toFixed(1)}%;
              top:${i * 11}px;height:9px;border-radius:2px;background:${COL[r.session] || "#94a3b8"};
              opacity:${r.complete ? 0.85 : 0.4}"></span>`).join("")}
          ${price == null ? "" : `<span style="position:absolute;left:${pos(price).toFixed(1)}%;top:0;bottom:0;width:1.5px;background:#0f172a"></span>`}
        </span></div>`;
    }).join("");

    return `<div>
      <div class="flex flex-wrap gap-x-3 text-[10.5px] muted mb-1">
        ${Object.entries(COL).map(([k, c]) => `<span><span style="display:inline-block;width:8px;height:8px;border-radius:1px;background:${c};vertical-align:middle"></span> ${esc(k === "newyork" ? "New York" : k)}</span>`).join("")}
        <span class="ml-auto">faded = still open · the dark line is now</span>
      </div>
      ${body}
      <div class="flex justify-between text-[10px] muted font-mono mt-1" style="margin-left:4.1rem">
        <span>${format(lo)}</span><span>${format(hi)}</span></div></div>`;
  }

  window.TBC = { timeLine, hbars, candles, ladder, strikeLadder, timeAtPrice, valueAreaMap, outcomeStrip, stateBar, sessionRanges, rangeBar, probBar, volumeProfile, colorFor, niceTicks, ROLE_COLOR };
})();
