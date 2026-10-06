/* Market analysis widgets, shared by the Market Data and Overview pages.
   Data: GET /api/analysis, then live OptionsSnapshot and MarketAnalysis events. */
(() => {
  const { esc, num, price, pct, compact, ago, time } = TB;
  const CALL = "#2a78d6", PUT = "#eb6834";  // validated pair (colour-blind safe); calls blue, puts orange everywhere
  const SEV = { alert: "badge-red", watch: "badge-amber", info: "" };
  const BIAS = { bullish: ["▲", "up"], bearish: ["▼", "down"], volatility: ["◆", "text-violet-600"], neutral: ["•", "muted"] };
  const ivp = (v, d = 1) => (v == null ? "—" : `${(100 * v).toFixed(d)}%`);
  const sgn = (v, d = 2) => (v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`);
  const STRAT = {
    NO_TRADE: ["No trade", ""], LONG_CALL_SPREAD: ["Long call spread", "badge-green"], LONG_PUT_SPREAD: ["Long put spread", "badge-red"],
    LONG_STRADDLE: ["Long straddle", "badge-blue"], LONG_STRANGLE: ["Long strangle", "badge-blue"], IRON_CONDOR: ["Iron condor", "badge-amber"],
  };

  const tile = (label, value, hint = "", cls = "", title = "") =>
    `<div class="rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2.5 min-w-0" title="${esc(title)}">
      <div class="stat-label">${label}</div><div class="font-mono text-[15px] font-semibold truncate ${cls}">${value}</div>
      <div class="text-[10.5px] muted truncate">${hint}</div></div>`;

  // ── KPI strip ──────────────────────────────────────────────────────────────
  function kpis(d) {
    const o = d.options?.summary, c = d.analysis?.context || {}, s = d.stats || {};
    const day = o?.day || {}, near = o?.nearest || {};
    const px = s.last ?? d.price ?? c.price;
    const ratio = c.iv_rv_ratio;
    return [
      tile("Price", price(px), `${sgn(s.change_24h_pct ?? c.change_24h_pct)} 24h · 1h ${sgn(c.change_1h_pct)}`, (s.change_24h_pct ?? 0) >= 0 ? "" : ""),
      tile("ATM IV", ivp(day.atm_iv), `${day.label || "—"} expiry · 7d RV ${ivp(c.rv_7d, 0)}`, "", "Implied vol at the strike nearest spot, for the first expiry at least 20h away"),
      tile("IV / RV", ratio == null ? "—" : ratio.toFixed(2), ratio == null ? "" : ratio >= 1.3 ? "options rich" : ratio <= 0.8 ? "options cheap" : "fair", ratio >= 1.3 ? "text-amber-600" : ratio <= 0.8 ? "text-violet-600" : ""),
      tile("25Δ skew", day.skew_25d == null ? "—" : `${day.skew_25d > 0 ? "+" : ""}${(100 * day.skew_25d).toFixed(1)}`, day.skew_25d > 0 ? "puts dearer" : day.skew_25d < 0 ? "calls dearer" : "vol points", "", "Put 25-delta IV minus call 25-delta IV"),
      tile("Put / call", o?.pcr_oi == null ? "—" : o.pcr_oi.toFixed(2), `OI · volume ${o?.pcr_volume == null ? "—" : o.pcr_volume.toFixed(2)}`),
      tile("Expected move", day.implied_move_pct == null ? "—" : `±${day.implied_move_pct.toFixed(2)}%`, day.implied_move ? `±${num(day.implied_move, 0)} by ${day.label} · straddle ${day.straddle_pct == null ? "—" : `${day.straddle_pct.toFixed(2)}%`}` : ""),
      tile("Max pain", near.max_pain == null ? "—" : num(near.max_pain, 0), `${near.label || ""} · ${num(near.hours, 0)}h · spot ${sgn(c.max_pain_gap_pct, 1)}`),
      tile("Walls", `<span style="color:${PUT}">${num(day.put_wall, 0)}</span> · <span style="color:${CALL}">${num(day.call_wall, 0)}</span>`, "put support · call resistance"),
      tile("Funding (24h avg)", c.funding_avg_24h_pct == null ? "—" : `${c.funding_avg_24h_pct.toFixed(4)}%`, `now ${s.funding_rate_pct == null ? "—" : pct(s.funding_rate_pct, 4)}`),
      tile("Perp OI", `$${compact(s.oi_usd ?? c.oi_usd)}`, `24h ${sgn(c.oi_change_24h_pct, 1)} · 1h ${sgn(c.oi_change_1h_pct, 1)}`),
      tile("Options OI", `$${compact(o?.oi_usd)}`, `${num(o?.contracts, 0)} contracts · 24h vol $${compact(o?.turnover_usd)}`),
      tile("Realised vol", ivp(c.rv_24h, 0), `24h · ATR ${c.atr_pct == null ? "—" : `${c.atr_pct.toFixed(2)}%`} (15m)`),
    ].join("");
  }

  // ── insights, AI, forecast ─────────────────────────────────────────────────
  function insights(d, limit = 99) {
    const a = d.analysis;
    if (!a) return `<div class="text-xs muted py-4 text-center">The first analysis runs within 5 minutes of the analyst starting.</div>`;
    const lean = a.context?.lean || {};
    const head = `<div class="flex items-center gap-2 text-[11.5px] mb-2"><span class="muted">Findings lean</span>
      <span class="badge ${lean.lean === "bullish" ? "badge-green" : lean.lean === "bearish" ? "badge-red" : ""}">${esc(lean.lean || "mixed")}</span>
      <span class="muted">▲ ${num(lean.bullish, 1)} · ▼ ${num(lean.bearish, 1)} · ${ago(a.ts)}</span></div>`;
    const rows = (a.insights || []).slice(0, limit).map((i) => {
      const [mark, cls] = BIAS[i.bias] || BIAS.neutral;
      return `<div class="flex gap-2.5 py-2 border-b border-slate-100 last:border-0">
        <span class="${cls} font-mono w-3 shrink-0 text-center" title="${esc(i.bias)}">${mark}</span>
        <div class="min-w-0"><div class="text-[12.5px] font-medium flex items-center gap-1.5 flex-wrap">${esc(i.title)}${i.severity !== "info" ? `<span class="badge ${SEV[i.severity]}">${esc(i.severity)}</span>` : ""}</div>
        <div class="text-[11.5px] text-slate-500">${esc(i.detail)}</div></div></div>`;
    }).join("");
    const errors = (a.context?.errors || []).length ? `<div class="text-[11px] down mt-1">Data missing: ${esc(a.context.errors.join("; "))}</div>` : "";
    return head + (rows || `<div class="text-xs muted py-3">Nothing stands out.</div>`) + errors;
  }

  function ai(d, status) {
    const r = d.analysis?.ai;
    if (status !== "on") {
      return `<div class="text-[11.5px] muted">TB-AI is ${status === "no key" ? "on but has no Mistral API key" : "off"}. <a class="text-brand-600" href="/settings#ai">Settings</a></div>`;
    }
    if (!r) return `<div class="text-[11.5px] muted">Waiting for the first TB-AI report.</div>`;
    const failed = r.error ? `<div class="text-[11px] text-amber-700 mb-1.5" title="${esc(r.error)}">Latest attempt failed (${esc(r.error.slice(0, 60))}); showing the last good review. <a class="text-brand-600" href="/ai">Details</a></div>` : "";
    if (!r.summary) return failed || `<div class="text-[11.5px] muted">No review for this symbol yet.</div>`;
    return `${failed}<div class="text-[12.5px] leading-relaxed">${esc(r.summary)}</div>
      ${r.points?.length ? `<ul class="mt-2 space-y-1 text-[12px] list-disc pl-4">${r.points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}
      ${r.risks?.length ? `<div class="mt-2 text-[11.5px]"><span class="font-semibold text-amber-700">Risks:</span> ${r.risks.map(esc).join(" · ")}</div>` : ""}
      ${r.playbook_view ? `<div class="mt-2 text-[11.5px] muted">On the playbook: ${esc(r.playbook_view)}</div>` : ""}
      <div class="mt-2 text-[10.5px] muted">${esc(r.model || "")} · ${ago(r.at)} · written from the numbers above; it does not decide trades</div>`;
  }

  function bar(label, p, color, note = "") {
    const w = Math.max(0, Math.min(100, 100 * (p || 0)));
    return `<div><div class="flex justify-between text-[11px]"><span class="muted">${label}</span><span class="font-mono">${p == null ? "—" : `${w.toFixed(0)}%`}${note}</span></div>
      <div class="h-1.5 rounded-full bg-slate-100 mt-1"><div class="h-1.5 rounded-full" style="width:${w}%;background:${color}"></div></div></div>`;
  }

  function forecast(d) {
    const a = d.analysis, f = a?.forecast, m = a?.context?.model || {}, p = a?.playbook;
    let model;
    if (!a) model = `<div class="text-xs muted">No analysis yet.</div>`;
    else if (m.status !== "ready") {
      model = `<div class="text-[12px]"><span class="badge badge-amber">${esc(m.status || "no model")}</span>
        <div class="text-[11.5px] muted mt-2">${esc(m.hint || "")}</div>
        <div class="text-[11px] muted mt-1">Rule-based insights do not need a model.</div></div>`;
    } else if (!f) {
      model = `<div class="text-[11.5px] down">Forecast failed: ${esc(m.hint || "")}</div>`;
    } else {
      const sk = f.skill || {};
      const flag = (k) => (sk[k] ? `<span class="up" title="beats its baseline on unseen data">✓</span>` : `<span class="muted" title="no edge over a naive baseline on unseen data: ignored by the playbook">✗</span>`);
      model = `<div class="space-y-2.5">
        ${bar(`Up in ${num(f.horizon_hours, 0)}h ${flag("direction")}`, f.up_probability, f.up_probability >= 0.5 ? "#12804a" : "#b42323")}
        ${bar(`Breakout ±${num(f.breakout_pct, 0)}% ${flag("breakout")}`, f.breakout_probability, "#7c3aed")}
        <div class="grid grid-cols-2 gap-2 pt-1">
          ${tile(`Forecast RV ${flag("realized_vol")}`, ivp(f.predicted_realized_vol, 0), `vs ATM IV ${ivp(p?.atm_iv ?? a.context?.atm_iv, 0)}`)}
          ${tile(`Abs move ${flag("abs_move")}`, `±${(100 * f.expected_abs_move).toFixed(2)}%`, `return ${sgn(100 * f.expected_return, 2)} ${sk.return ? "" : "(no skill)"}`)}
        </div>
        <div class="text-[10.5px] muted">Trained ${ago(m.trained_at)} on ${new Date(m.train_from * 1000).toLocaleDateString("en-GB")}–${new Date(m.train_to * 1000).toLocaleDateString("en-GB")} · holdout AUC dir ${num(m.metrics?.direction_auc, 3)} · breakout ${num(m.metrics?.breakout_auc, 3)}</div>
      </div>`;
    }
    let play = "";
    if (p) {
      const [name, cls] = STRAT[p.strategy] || [p.strategy, ""];
      const legs = (p.legs || []).map((l) => `<tr><td><span class="${l.action === "buy" ? "up" : "down"} font-semibold">${l.action.toUpperCase()}</span></td>
        <td style="color:${l.kind === "call" ? CALL : PUT}">${l.kind}</td><td class="r font-mono">${num(l.strike, 0)}</td><td class="r font-mono">${price(l.mark)}</td>
        <td class="r font-mono muted">${ivp(l.iv)}</td><td class="font-mono text-[10.5px] muted">${esc(l.symbol)}</td></tr>`).join("");
      play = `<div class="mt-4 pt-3 border-t border-slate-100">
        <div class="flex items-center justify-between gap-2"><div class="stat-label">Options playbook</div><span class="badge ${cls}">${esc(name)}</span></div>
        <div class="text-[11.5px] text-slate-600 mt-1.5">${esc(p.reason)}</div>
        ${legs ? `<table class="tbl mt-2 text-[11.5px]"><tbody>${legs}</tbody></table>
          <div class="grid grid-cols-3 gap-2 mt-2">${tile(p.net >= 0 ? "Credit" : "Debit", price(Math.abs(p.net)), "per 1 underlying, at mark")}${tile("Max loss", p.max_loss == null ? "—" : price(p.max_loss), "")}${tile("Max profit", p.max_profit == null ? "open" : price(p.max_profit), (p.breakevens || []).map((b) => num(b, 0)).join(" / ") + " BE")}</div>` : ""}
        <div class="text-[10.5px] muted mt-2">Advisory: TradeBuddy does not place option orders. ${p.expiry ? `Expiry ${esc(p.expiry)} (${num(p.hours, 0)}h).` : ""}</div>
      </div>`;
    }
    return model + play;
  }

  // ── option chain ───────────────────────────────────────────────────────────
  function chain(o, expiry) {
    const rows = o?.summary?.chains?.[expiry] || [];
    const e = (o?.summary?.expiries || []).find((x) => String(Math.trunc(x.expiry)) === String(expiry)) || {};
    const spot = o?.summary?.spot;
    if (!rows.length) return `<tr><td colspan="13" class="empty">No contracts near spot for this expiry</td></tr>`;
    const maxOi = Math.max(...rows.flatMap((r) => [r.call?.oi || 0, r.put?.oi || 0]), 1e-9);
    const atm = rows.reduce((b, r) => (Math.abs(r.strike - spot) < Math.abs(b.strike - spot) ? r : b), rows[0]).strike;
    const side = (s, color, alignRight) => {
      if (!s) return `<td colspan="5" class="muted text-center">—</td>`;
      const w = (100 * (s.oi || 0)) / maxOi;
      const oiCell = `<td class="r font-mono relative"><span class="absolute inset-y-1 ${alignRight ? "right-0" : "left-0"} rounded-sm opacity-20" style="width:${w}%;background:${color}"></span><span class="relative">${num(s.oi, 2)}</span></td>`;
      const cells = [`<td class="r font-mono muted">${num(s.volume, 2)}</td>`, `<td class="r font-mono">${ivp(s.iv)}</td>`, `<td class="r font-mono muted">${num(s.delta, 2)}</td>`, `<td class="r font-mono">${price(s.mark)}</td>`];
      // Calls read outward from the strike to the left, puts to the right: OI sits next to the strike.
      return alignRight ? [...cells].reverse().join("") + oiCell : oiCell + cells.join("");
    };
    return rows.map((r) => {
      const tags = [r.strike === e.max_pain ? "max pain" : "", r.strike === e.call_wall ? "call wall" : "", r.strike === e.put_wall ? "put wall" : ""].filter(Boolean);
      return `<tr class="${r.strike === atm ? "bg-sky-50" : ""}">${side(r.call, CALL, true)}
        <td class="text-center font-mono font-semibold whitespace-nowrap ${r.strike < spot ? "text-slate-500" : ""}">${num(r.strike, 0)}${tags.length ? `<div class="text-[9.5px] text-amber-700 font-sans font-normal">${tags.join(" · ")}</div>` : ""}</td>
        ${side(r.put, PUT, false)}</tr>`;
    }).join("");
  }

  // OI by strike: calls up, puts down, spot marked. Inline SVG on one linear scale.
  function oiChart(o, expiry) {
    const rows = o?.summary?.chains?.[expiry] || [];
    if (!rows.length) return "";
    const W = 640, H = 200, pad = 26, mid = H / 2, bw = Math.max(2, (W - 2 * pad) / rows.length - 2);
    const max = Math.max(...rows.flatMap((r) => [r.call?.oi || 0, r.put?.oi || 0]), 1e-9);
    const x = (i) => pad + i * ((W - 2 * pad) / rows.length);
    const spot = o.summary.spot, lo = rows[0].strike, hi = rows[rows.length - 1].strike;
    const sx = pad + ((spot - lo) / (hi - lo || 1)) * (W - 2 * pad - bw) + bw / 2;
    let s = `<svg viewBox="0 0 ${W} ${H}" class="w-full h-auto" role="img" aria-label="Open interest by strike">`;
    s += `<line x1="${pad}" x2="${W - pad}" y1="${mid}" y2="${mid}" stroke="#cbd5e1"/>`;
    rows.forEach((r, i) => {
      const c = ((r.call?.oi || 0) / max) * (mid - 18), p = ((r.put?.oi || 0) / max) * (mid - 18);
      s += `<rect x="${x(i)}" y="${mid - c}" width="${bw}" height="${c}" rx="1.5" fill="${CALL}"><title>${num(r.strike, 0)} calls OI ${num(r.call?.oi, 2)}</title></rect>`;
      s += `<rect x="${x(i)}" y="${mid}" width="${bw}" height="${p}" rx="1.5" fill="${PUT}"><title>${num(r.strike, 0)} puts OI ${num(r.put?.oi, 2)}</title></rect>`;
    });
    const ticks = [0, Math.floor(rows.length / 2), rows.length - 1];
    ticks.forEach((i) => { s += `<text x="${x(i) + bw / 2}" y="${H - 4}" font-size="10" text-anchor="middle" fill="#64748b">${num(rows[i].strike, 0)}</text>`; });
    s += `<line x1="${sx}" x2="${sx}" y1="8" y2="${H - 16}" stroke="#0f172a" stroke-dasharray="3 3"/><text x="${sx + 4}" y="14" font-size="10" fill="#0f172a">spot ${num(spot, 0)}</text>`;
    s += `<text x="${pad}" y="12" font-size="10" fill="${CALL}">calls ↑</text><text x="${pad}" y="${H - 18}" font-size="10" fill="${PUT}">puts ↓</text>`;
    return s + "</svg>";
  }

  // IV smile: mark IV by strike, calls and puts.
  function smileChart(o, expiry) {
    const rows = o?.summary?.chains?.[expiry] || [];
    const pts = (k) => rows.filter((r) => r[k]?.iv).map((r) => [r.strike, r[k].iv]);
    const c = pts("call"), p = pts("put");
    if (c.length + p.length < 3) return "";
    const W = 640, H = 200, pad = 32, all = [...c, ...p];
    const xs = all.map((v) => v[0]), ys = all.map((v) => v[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys) * 0.95, y1 = Math.max(...ys) * 1.05;
    const X = (v) => pad + ((v - x0) / (x1 - x0 || 1)) * (W - 2 * pad), Y = (v) => H - 20 - ((v - y0) / (y1 - y0 || 1)) * (H - 36);
    const line = (arr, col) => `<polyline fill="none" stroke="${col}" stroke-width="2" points="${arr.map(([a, b]) => `${X(a)},${Y(b)}`).join(" ")}"/>` +
      arr.map(([a, b]) => `<circle cx="${X(a)}" cy="${Y(b)}" r="3" fill="${col}" stroke="#fff" stroke-width="1.5"><title>${num(a, 0)}: ${ivp(b)}</title></circle>`).join("");
    const spot = o.summary.spot;
    let s = `<svg viewBox="0 0 ${W} ${H}" class="w-full h-auto" role="img" aria-label="Implied volatility by strike">`;
    [y0, (y0 + y1) / 2, y1].forEach((v) => { s += `<line x1="${pad}" x2="${W - pad}" y1="${Y(v)}" y2="${Y(v)}" stroke="#f1f5f9"/><text x="2" y="${Y(v) + 3}" font-size="10" fill="#64748b">${ivp(v, 0)}</text>`; });
    s += `<line x1="${X(spot)}" x2="${X(spot)}" y1="8" y2="${H - 20}" stroke="#0f172a" stroke-dasharray="3 3"/>`;
    s += line(c, CALL) + line(p, PUT);
    [x0, x1].forEach((v) => { s += `<text x="${X(v)}" y="${H - 4}" font-size="10" text-anchor="middle" fill="#64748b">${num(v, 0)}</text>`; });
    return s + "</svg>";
  }

  // Term structure: ATM IV by expiry.
  function termChart(o) {
    const t = (o?.summary?.term || []).filter((e) => e.atm_iv);
    if (t.length < 2) return "";
    const W = 640, H = 140, pad = 32, ys = t.map((e) => e.atm_iv), y0 = Math.min(...ys) * 0.95, y1 = Math.max(...ys) * 1.05;
    const X = (i) => pad + (i / (t.length - 1)) * (W - 2 * pad), Y = (v) => H - 22 - ((v - y0) / (y1 - y0 || 1)) * (H - 40);
    let s = `<svg viewBox="0 0 ${W} ${H}" class="w-full h-auto" role="img" aria-label="ATM implied volatility by expiry">`;
    s += `<polyline fill="none" stroke="#7c3aed" stroke-width="2" points="${t.map((e, i) => `${X(i)},${Y(e.atm_iv)}`).join(" ")}"/>`;
    t.forEach((e, i) => {
      s += `<circle cx="${X(i)}" cy="${Y(e.atm_iv)}" r="3.5" fill="#7c3aed" stroke="#fff" stroke-width="1.5"><title>${esc(e.label)}: ${ivp(e.atm_iv)}</title></circle>`;
      if (i === 0 || i === t.length - 1 || t.length <= 8) s += `<text x="${X(i)}" y="${H - 6}" font-size="10" text-anchor="middle" fill="#64748b">${esc(e.label)}</text><text x="${X(i)}" y="${Y(e.atm_iv) - 7}" font-size="10" text-anchor="middle" fill="#334155">${ivp(e.atm_iv, 0)}</text>`;
    });
    return s + "</svg>";
  }

  // A small line for a 24h series (ATM IV, put/call) from options_history.
  function spark(rows, key, color, fmt) {
    const pts = rows.filter((r) => r[key] != null);
    if (pts.length < 2) return `<div class="text-[11px] muted">Collecting history (a row a minute)…</div>`;
    const W = 300, H = 56, vs = pts.map((r) => r[key]), lo = Math.min(...vs), hi = Math.max(...vs);
    const X = (i) => (i / (pts.length - 1)) * W, Y = (v) => H - 4 - ((v - lo) / (hi - lo || 1)) * (H - 8);
    const d = pts.map((r, i) => `${X(i)},${Y(r[key])}`).join(" ");
    return `<svg viewBox="0 0 ${W} ${H}" class="w-full h-14" preserveAspectRatio="none"><polyline fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" points="${d}"/>
      <circle cx="${X(pts.length - 1)}" cy="${Y(vs[vs.length - 1])}" r="3" fill="${color}"/></svg>
      <div class="flex justify-between text-[10.5px] muted font-mono"><span>${fmt(lo)}</span><span>now ${fmt(vs[vs.length - 1])}</span><span>${fmt(hi)}</span></div>`;
  }

  function sourceBadge(o) {
    if (!o) return `<span class="badge badge-amber">no options data</span>`;
    const age = Date.now() / 1000 - o.at;
    return `<span class="badge ${age > 90 ? "badge-amber" : o.source === "websocket" ? "badge-green" : "badge-blue"}" title="Options summary ${Math.round(age)}s old">options · ${esc(o.source)} · ${ago(o.at)}</span>`;
  }

  window.TBA = { CALL, PUT, kpis, insights, ai, forecast, chain, oiChart, smileChart, termChart, spark, sourceBadge, STRAT, ivp, sgn };
})();
