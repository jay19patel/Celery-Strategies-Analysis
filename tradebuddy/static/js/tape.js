// The tape: the price chart, on TradingView's Lightweight Charts.
//
// Everything else drawn on this dashboard is hand-rolled SVG, and should stay that way — a bar
// chart does not need 160 KB of library. A *price* chart does. Zooming, panning, a crosshair that
// snaps to bars, a price scale that stays readable at every zoom, touch gestures, and redrawing
// ten thousand candles at 60fps on a canvas are each a week of work and all of them are table
// stakes for reading a market. The hand-rolled version that stood here had a wheel handler and a
// drag handler and was already the most delicate code on the page.
//
// The library draws candles, volume, horizontal levels and markers. It has no notion of a shaded
// zone or a volume-by-price gutter, so those go on our own canvas UNDERNEATH it: the chart's
// background is transparent, our canvas sits behind, and both are driven by the same price and
// time scales. That way a zone cannot drift from the candle it belongs to — the only coordinates
// in play are the library's own `priceToCoordinate` and `timeToCoordinate`.
//
// If the library is missing the page says so and still works; nothing else on it depends on this.

(() => {
  const HAS_LIB = typeof LightweightCharts !== "undefined";

  const UP = "#00b852", DOWN = "#eb5757", INK = "#0f172a", AXIS = "#94a3b8", GRID = "#eef2f7";
  const KIND_COLOR = {
    pool: "#d97706", zone: "#7c3aed", poc: "#0891b2", value: "#0891b2",
    naked: "#0891b2", reference: "#64748b", wall: "#1e88e5",
  };
  // Coloured by WHAT THE ZONE IS, not which way it leans. Direction is already obvious from where
  // the band sits relative to price, while the kind is not readable from the chart at all — and an
  // order block and a fair value gap are different propositions that were being drawn identically.
  // Light fills on purpose: these sit behind the candles and must never compete with them.
  const ZONE_FILL = {
    ob: [217, 119, 6],    // amber — the last opposite candle before a displacement
    fvg: [124, 58, 237],  // violet — a three-bar imbalance
    sr: [13, 148, 136],   // teal — a clustered support/resistance band
  };
  const ZONE_LABEL = { ob: "order block", fvg: "fair value gap", sr: "S/R band" };
  const ZONE_ALPHA = { fresh: 0.16, tested: 0.1, mitigated: 0.05, invalidated: 0.025 };
  const GUTTER = 92;  // px of plot width given to the volume-by-price histogram

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fmt = (v) => (v == null ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 }));

  /**
   * Put a chart in `el`. Returns a controller; call `update()` whenever the data changes.
   *
   * The controller keeps the chart alive across updates — rebuilding it on every poll would throw
   * away the zoom and the scroll position, which is the one thing a person sets by hand.
   */
  function mount(el, opts = {}) {
    if (!HAS_LIB) {
      el.innerHTML = `<div class="text-[11px] muted py-8 text-center">The chart library did not load.
        Everything else on this page still works; reload to try again.</div>`;
      return { update() {}, destroy() {}, ok: false };
    }

    el.innerHTML = "";
    el.style.position = "relative";
    const height = opts.height || 440;

    // Behind the chart: zones and the volume-by-price gutter.
    const canvas = document.createElement("canvas");
    canvas.style.cssText = "position:absolute;inset:0;pointer-events:none;z-index:0";
    el.appendChild(canvas);

    const holder = document.createElement("div");
    holder.style.cssText = "position:relative;z-index:1";
    el.appendChild(holder);

    // Inline styles, not Tailwind classes. This element is created by JavaScript after the CDN
    // build has already scanned the document, so its arbitrary-value classes (`bg-white/97`) were
    // never generated — the card rendered with no background at all and the chart showed straight
    // through the text. Anything built at runtime styles itself.
    const tip = document.createElement("div");
    tip.style.cssText = [
      "position:absolute", "z-index:3", "pointer-events:none", "display:none",
      "background:#fff", "border:1px solid #e2e8f0", "border-radius:6px",
      "box-shadow:0 4px 14px rgba(15,23,42,.13)", "padding:6px 9px",
      "font-size:11px", "line-height:1.45", "white-space:nowrap", "color:#0f172a",
    ].join(";");
    el.appendChild(tip);

    const chart = LightweightCharts.createChart(holder, {
      height,
      layout: { background: { color: "transparent" }, textColor: AXIS, fontSize: 10,
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" },
      grid: { vertLines: { color: GRID }, horzLines: { color: GRID } },
      rightPriceScale: { borderColor: GRID, scaleMargins: { top: 0.08, bottom: 0.26 } },
      timeScale: { borderColor: GRID, timeVisible: true, secondsVisible: false, rightOffset: 4 },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: { color: AXIS, width: 1, style: 2, labelBackgroundColor: INK },
        horzLine: { color: AXIS, width: 1, style: 2, labelBackgroundColor: INK },
      },
      handleScroll: true, handleScale: true,
    });

    const candles = chart.addCandlestickSeries({
      upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN,
      wickUpColor: UP, wickDownColor: DOWN,
      priceFormat: { type: "price", precision: 1, minMove: 0.1 },
    });
    // Volume shares the pane but lives in its own scale at the bottom, so it never competes with
    // price for vertical room.
    const volume = chart.addHistogramSeries({
      priceScaleId: "vol", priceFormat: { type: "volume" },
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

    let state = { bars: [], zones: [], levels: [], profile: null, naked: [], shapes: {} };
    let priceLines = [];

    function paintOverlay() {
      const dpr = window.devicePixelRatio || 1;
      const w = el.clientWidth, h = height;
      if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
        canvas.width = w * dpr; canvas.height = h * dpr;
        canvas.style.width = `${w}px`; canvas.style.height = `${h}px`;
      }
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      if (!state.bars.length) return;

      const ts = chart.timeScale();
      const plotR = w - (chart.priceScale("right").width() || 60);

      // Zones: from where they formed to the right edge of the plot.
      for (const z of state.zones) {
        const yTop = candles.priceToCoordinate(z.high), yBot = candles.priceToCoordinate(z.low);
        if (yTop == null || yBot == null) continue;
        let x = z.created_at ? ts.timeToCoordinate(z.created_at) : 0;
        if (x == null) x = 0;  // formed before the visible window: run it off the left edge
        const rgb = ZONE_FILL[z.kind] || [148, 163, 184];
        const a = ZONE_ALPHA[z.state] ?? 0.1;
        ctx.fillStyle = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a})`;
        ctx.fillRect(Math.max(0, x), yTop, Math.max(1, plotR - Math.max(0, x)), Math.max(1.5, yBot - yTop));
        ctx.strokeStyle = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${Math.min(0.45, a * 2.2)})`;
        ctx.lineWidth = 1;
        ctx.strokeRect(Math.max(0, x), yTop + 0.5, Math.max(1, plotR - Math.max(0, x)), Math.max(1.5, yBot - yTop));
      }

      // Naked points of control — a price the market agreed on and then left without returning.
      // Dotted, because it is a magnet rather than a level that has already held something.
      for (const n of (state.naked || [])) {
        const y = candles.priceToCoordinate(n.price);
        if (y == null) continue;
        ctx.save();
        ctx.setLineDash([2, 4]);
        ctx.strokeStyle = "rgba(8,145,178,0.75)";
        ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(plotR, y); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "rgba(8,145,178,0.95)";
        ctx.font = "9px ui-monospace, monospace";
        ctx.fillText(`naked POC ${fmt(n.price)}`, 4, y - 3);
        ctx.restore();
      }

      // Volume by price, in a gutter against the right edge of the plot.
      const prof = state.profile;
      if (prof?.levels?.length) {
        const peak = Math.max(...prof.levels.map((l) => l.volume || 0), 1);
        const ys = prof.levels.map((l) => candles.priceToCoordinate(l.price)).filter((y) => y != null);
        const band = ys.length > 1 ? Math.abs(ys[0] - ys[1]) : 4;
        for (const l of prof.levels) {
          const y = candles.priceToCoordinate(l.price);
          if (y == null) continue;
          const bw = ((l.volume || 0) / peak) * GUTTER;
          ctx.fillStyle = l.poc ? "rgba(217,119,6,0.85)" : l.in_value ? "rgba(30,136,229,0.5)" : "rgba(148,163,184,0.38)";
          ctx.fillRect(plotR - bw, y - band / 2, bw, Math.max(1, band * 0.86));
        }
      }
    }

    function rebuildLines() {
      for (const line of priceLines) candles.removePriceLine(line);
      // Every level gets a line; only some get words. Eleven labelled lines inside a few hundred
      // dollars stack into an unreadable block against the price scale — which is what the first
      // version did. The ones that earn a label are the nearest each way (what a stop or a
      // target would actually use) and anything more than one source agrees on. The rest are
      // still drawn, still in the tooltip, and all of them are listed in Around price below.
      const ranked = [...state.levels].sort((a, b) => (a.distance_atr ?? 0) - (b.distance_atr ?? 0));
      const named = new Set();
      for (const side of ["above", "below"]) {
        ranked.filter((l) => l.side === side).slice(0, 1).forEach((l) => named.add(l));
      }
      for (const l of ranked) if ((l.confluence || 1) > 1) named.add(l);

      priceLines = ranked.map((l) => {
        const strong = (l.confluence || 1) > 1;
        const label = named.has(l);
        return candles.createPriceLine({
          price: l.price,
          color: KIND_COLOR[l.kind] || AXIS,
          lineWidth: strong ? 2 : 1,
          lineStyle: strong ? LightweightCharts.LineStyle.Solid : LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: label,
          title: label ? `${strong ? "◆ " : ""}${l.label || ""}`.slice(0, 22) : "",
        });
      });
    }

    chart.timeScale().subscribeVisibleLogicalRangeChange(paintOverlay);
    chart.subscribeCrosshairMove((param) => {
      if (!param.point || !param.time || !state.bars.length) { tip.style.display = "none"; return; }
      const bar = param.seriesData.get(candles);
      if (!bar) { tip.style.display = "none"; return; }
      const price = candles.coordinateToPrice(param.point.y);
      tip.innerHTML = opts.tooltip ? opts.tooltip(bar, param.time, price) : "";
      if (!tip.innerHTML) { tip.style.display = "none"; return; }
      tip.style.display = "block";
      const w = tip.offsetWidth, h = tip.offsetHeight;
      tip.style.left = `${Math.max(4, Math.min(el.clientWidth - w - 4, param.point.x + 14))}px`;
      tip.style.top = `${Math.max(4, Math.min(height - h - 4, param.point.y + 14))}px`;
    });

    const ro = new ResizeObserver(() => { chart.applyOptions({ width: el.clientWidth }); paintOverlay(); });
    ro.observe(el);
    chart.applyOptions({ width: el.clientWidth });

    return {
      ok: true,
      chart, candles,
      update(next) {
        const first = !state.bars.length;
        state = { ...state, ...next };
        candles.setData(state.bars.map((b) => ({
          time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
        })));
        volume.setData((next.sub || []).map((b) => ({
          time: b.time, value: b.value,
          color: b.up === false ? "rgba(235,87,87,.45)" : b.up === true ? "rgba(0,184,82,.45)" : "rgba(148,163,184,.45)",
        })));
        candles.setMarkers((next.marks || []).map((m) => ({
          time: m.bar_time,
          position: m.above ? "aboveBar" : "belowBar",
          color: m.color, shape: m.shape || "circle", text: m.text || "",
        })).sort((a, b) => a.time - b.time));
        rebuildLines();
        // Only the first load chooses the window. After that the view is the person's, and a poll
        // that quietly refit it would undo their zoom every minute.
        if (first) {
          const n = state.bars.length;
          chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - (opts.openBars || 140)), to: n + 2 });
        }
        paintOverlay();
      },
      fit() { chart.timeScale().fitContent(); },
      zoom(factor) {
        const ts = chart.timeScale(), r = ts.getVisibleLogicalRange();
        if (!r) return;
        const mid = (r.from + r.to) / 2, half = ((r.to - r.from) / 2) * factor;
        ts.setVisibleLogicalRange({ from: mid - half, to: mid + half });
      },
      nudge(bars) {
        const ts = chart.timeScale(), r = ts.getVisibleLogicalRange();
        if (!r) return;
        ts.setVisibleLogicalRange({ from: r.from + bars, to: r.to + bars });
      },
      destroy() { ro.disconnect(); chart.remove(); },
    };
  }

  window.TBTape = { mount, available: HAS_LIB, KIND_COLOR, ZONE_FILL, ZONE_LABEL, esc, fmt };
})();
