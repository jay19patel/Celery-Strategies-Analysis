# Analytics audit, and the plan for a terminal

Step 0 of the analytics upgrade: read the code first, decide what to keep, then design the screen.
**Nothing below has been implemented.** This is the thing to approve or argue with.

Written against branch `v3` at `23d45c6`, 21 Oct 2026. Every file path is real; every number is
measured on `data/tradebuddy.db` (BTCUSD + ETHUSD, 35,468 stored 15m bars each,
2025-10-15 → 2026-10-19) or read out of the code.

---

## 0. Three things to know before reading anything else

**0.1 — The brief was written against a branch you are not on.**
It asks for `app/analytics/…`, cites `SYSTEM_ARCHITECTURE_AND_FLOW.md`, and refers to `frontend/`
and `app/strategies/`. Those exist on `main`. `v3` — the branch you are on, and the one that runs —
is a complete rewrite into a flat `tradebuddy/` package: pure modules, no service layer, no router
package, Jinja templates plus hand-rolled SVG. `main` is 178 files and ~15k lines away.

Building `app/analytics/` on `v3` would be a **third** architecture living beside two others. This
plan maps every item in the brief onto where it belongs in `v3` instead. Same features, same
discipline, no parallel universe.

**0.2 — The ML and AI layers were deleted mid-audit, in `23d45c6`.**
`forecast.py`, `tbai.py`, `mistral.py`, `playbook.py`, `templates/ai.html`, the shipped
`models/`, and `docs/ANALYTICS_PLAN.md` are gone; `CLAUDE.md` is updated and clean; the orphaned
`forecasts` and `ai_reports` tables are dropped on open (`store.py:353`). I checked — there are no
stale references left. Three consequences for this plan:

- the brief's "**ML (existing models)**: use the new states as features" has nothing to feed.
  Any model is now a *new* build, not a rewiring (§9 no longer asks about it);
- `provenance.py` is down to three sources (MATH / COUNTED / IMPLIED). That is the right number —
  but a confluence score is a *fitted* thing, so if one ships it needs a fourth source and its
  own honesty rule (§3 #6);
- **the previous plan is no longer in the repo.** Its phases 0 and 1 shipped (`bars`,
  `market_snapshots`, `process_health`, `predictions`, private-by-default dashboard); phases 2–6
  are now unrecorded anywhere. This document replaces it.

**0.3 — Most of the indicator work in the brief already exists.** The list in §1 is what I read,
not what might be there. Swings, BOS, CHoCH, FVG, squeeze, resampling, VWAP, volume profile with
POC/VAH/VAL, multi-timeframe indicator agreement, triple-barrier outcomes, per-claim scoring with
a baseline and a sample-size floor, and provenance badging are all built and tested.

**The gap is not indicators. It is persistence of what was computed, real order flow, and a screen
worth looking at.**

---

## 1. The analytics inventory

Everything in `tradebuddy/` that computes, stores or serves a market number.

### 1.1 Compute — pure modules

| Module | Lines | What it computes | Read by |
| --- | ---: | --- | --- |
| `structure.py` | 362 | swings (rolling H/L), **BOS as a fresh cross**, CHoCH, FVG with filled flag, displacement, volume ratio, BB squeeze, `resample(factor)`, candle `Shape`s, realised vol | `outlook`, `reading`, `trade_buddy_manager`, Market page |
| `reading.py` | 609 | per-tab blocks: `volume_block`, `indicator_block`, `vwap_block`, **`volume_profile` (POC/VAH/VAL, 48 bins, 2-day window)**, `anatomy`, `levels_block`, `shape_record`, **`barrier_outcome` (triple barrier)**, `regime_at`, `indicators_by_timeframe`, `indicator_agreement` | Market tabs, `verdict` |
| `outlook.py` | 651 | 3-timeframe trend (15m/1h/1d), `sigma_over` (√t), `p_touch`, `p_touch_either`, `p_stop_first`, `counted_direction` with baseline + `MIN_SAMPLE=200`, alignment, `pattern_record`, `narrate` | Market page, `trade_buddy_manager`, `verdict` |
| `verdict.py` | 622 | one checkable claim per card (11 cards), `settle()`, `score()` with `MIN_MARKED=30` | Market page, `predictions` |
| `insights.py` | 253 | `market_context` (~45 named numbers), ~15 rules, `lean`, `outcome` | analyst, `market_snapshots` |
| `options.py` | 349 | ATM IV, 25Δ skew, PCR, max pain, call/put walls, implied move, term curve, chain rows | analyst, Market page |
| `structures.py` | 328 | defined-risk option structures + suggestion | ticket, `AutoStructures` |
| `provenance.py` | 91 | 3 sources, card → source map, badge + legend | Market page |
| `strategies/indicators.py` | 165 | `ema`, `rsi`, `atr`, `adx`, `macd`, `bollinger`, `obv`, `supertrend`, cross helpers — **pure Python lists, no numpy** | everything above |
| `analytics.py` | 498 | *(misnamed)* one trading day read back for the Journal page and the email. **Not market analytics.** | Journal, email |

All of the first group are genuinely pure — lists in, dataclasses out, no clock, no network, no DB
handle. That is the most valuable property this codebase has and the plan must not spend it.

### 1.2 Orchestration and transport

| Piece | Where | Cadence |
| --- | --- | --- |
| `Analyst` | `analyst.py` (131 ln) | every 300s, 20s past the mark; 700×15m candles + OI + funding per symbol over REST → `MarketAnalysis` |
| `System.collect_bars` | `system.py` | every 900s — writes `bars`, `INSERT OR IGNORE` |
| `System.outlook` | `system.py:623` | cached per `(symbol, bar_time)`, rebuilt once a bar |
| claim settler | `system.py` | settles `predictions` once the horizon passes |
| `DeltaStream` | `stream.py` | subscribes **only** `v2/ticker` + `candlestick_<res>`, and only for resolutions a strategy declares |
| `BarCloser` | `stream.py:77` | rolls a bar → `CandleClosed` |
| Celery | `runner.py`, `worker.py` | one task = one strategy on one bar; results expire after a bar |

### 1.3 Storage

| Table | Rows now | Retention | Note |
| --- | ---: | --- | --- |
| `bars` | 70,936 | forever | **15m only. No 1m, 5m, 1h, 4h or 1d rows.** |
| `market_snapshots` | 178 | 30d / 15m-boundary forever | 26 typed columns, written per *analyst cycle*, not per *bar* |
| `options_history` | — | 30d / 15m forever | summary only, **not per-strike** |
| `predictions` | 554 (358 settled) | 180d | card verdicts across 11 sources |
| `process_health` | — | 30d | CPU / RSS / loop lag |
| `orders`, `paper_*`, `daily_risk`, `events`, `settings`, `toggles`, `position_controls` | — | permanent / 2–90d | execution record |

### 1.4 Serving

51 routes in `app.py`, all behind the PIN by default (invariant 9b). The analytics reads:
`/api/analysis`, `/api/outlook`, `/api/verdicts`, `/api/bars`, `/api/snapshots`,
`/api/options/history`, `/api/health`, `/api/database`, plus `/ws`.

### 1.5 Front end

Jinja templates extending `base.html`. `static/js/chart.js` (21 KB) is a hand-rolled SVG kit:
`timeLine`, `hbars`, `candles` with levels/marks/projection, `niceTicks`, `axisTime`.
`analysis.js` adds `oiChart`, `smileChart`, `termChart`, `spark`, `chain`, `kpis`, `sourceBadge`.
`market.html` is 64 KB and already tabbed by provenance
(Patterns · Volume · Indicators · Open interest · Findings).

---

## 2. Keep, merge, remove, rewrite — with the numbers

### 2.1 The evidence

Spearman rank correlation of each indicator against the **forward return**, over every stored 15m
bar, both symbols independently. The script imports the repo's own `strategies/indicators.py`, so
these are the exact definitions that trade.

Forward windows overlap (a 4h forward return on 15m bars reuses 16 bars), so the effective sample
is ~2,200, not 35,358 — **the standard error on each ρ is ≈ 0.021, not 0.005.** Anything under
|0.04| is noise. That threshold is applied below.

```
                    BTCUSD              ETHUSD
feature            IC 4h    IC 24h     IC 4h    IC 24h
rsi14             -0.044   +0.005     -0.055   -0.021
rsi7              -0.040   +0.000     -0.045   -0.017
rsi21             -0.042   +0.004     -0.058   -0.024
macd_line         -0.046   +0.004     -0.061   -0.019
macd_hist         -0.021   -0.006     -0.020   -0.004
di_spread         -0.027   +0.015     -0.049   -0.000
bb_pos            -0.040   +0.001     -0.045   -0.017
dist_ema20        -0.050   +0.001     -0.060   -0.017
dist_ema50        -0.047   -0.003     -0.061   -0.024
dist_ema200       -0.045   -0.046     -0.058   -0.056
ema20_50_spread   -0.041   -0.005     -0.056   -0.028
range_pos_24h     -0.027   +0.001     -0.043   -0.021
cvd_slope20       -0.045   +0.009     -0.055   +0.002
obv_slope         -0.031   -0.023     -0.031   -0.009
adx14             +0.002   +0.001     +0.008   -0.013
bb_width          -0.010   -0.030     -0.017   -0.062
atr_pct           +0.004   -0.038     -0.008   -0.063
vol_ratio20       -0.005   +0.005     +0.001   +0.002
body_pct          +0.003   -0.004     -0.010   -0.004
delta_ratio       -0.017   -0.001     -0.012   +0.002
oi_chg_24h        -0.030   +0.012     -0.035   +0.041
```

Three readings. The second one is uncomfortable and matters more than the rest of this document.

1. **No single indicator carries direction.** The largest |IC| anywhere is 0.063. At SE ≈ 0.021
   the best of them is a 3σ effect explaining 0.4% of variance. This is the answer `CLAUDE.md`
   already records for the deleted 24h forecaster, now confirmed for every classical indicator on
   the stored bars. **Any screen that renders one of these as a direction call is lying.**

2. **Every momentum and location feature has a negative 4h IC, on both symbols, without
   exception.** Twelve features, two independent symbols, same sign. That is not twelve coin
   flips. On this data, over 4 hours, 15m bars **mean-revert** — price stretched above its EMAs
   tends to come back. `tb_master` and `trade_buddy_manager` both trade *breakouts* on the 15m/1h
   frames. The effect is small (≈0.05) and could be a 2025-26 regime artefact, but it is the
   **opposite sign to the system's core assumption and nothing currently measures it.** It belongs
   on the screen as a counted base rate, and it belongs in a backtest before any new auto strategy
   ships.

3. **The strongest 24h signals are volatility, not direction** — `atr_pct` and `bb_width` at
   −0.04 to −0.06, and `oi_chg_24h` at +0.04 on ETH. Volatility sizes a trade; open interest is
   derivatives positioning. Both survive the cut; the oscillators do not.

Redundancy (BTCUSD, |ρ| among the momentum/location family — ETHUSD is identical to two decimals):

```
                 rsi7  rsi14  rsi21  macdH  macdL  di_sp bb_pos  dE20   dE50  e20_50  delta  cvd
rsi7            +1.00  +0.92  +0.82  +0.70  +0.57  +0.80  +0.96  +0.93  +0.73  +0.35  +0.32  +0.53
rsi14           +0.92  +1.00  +0.97  +0.54  +0.82  +0.93  +0.90  +0.96  +0.91  +0.63  +0.21  +0.58
rsi21           +0.82  +0.97  +1.00  +0.39  +0.90  +0.93  +0.80  +0.90  +0.97  +0.78  +0.16  +0.55
di_spread       +0.80  +0.93  +0.93  +0.46  +0.83  +1.00  +0.80  +0.87  +0.89  +0.68  +0.03  +0.48
bb_pos          +0.96  +0.90  +0.80  +0.74  +0.58  +0.80  +1.00  +0.91  +0.71  +0.33  +0.26  +0.54
dist_ema20      +0.93  +0.96  +0.90  +0.64  +0.74  +0.87  +0.91  +1.00  +0.86  +0.51  +0.23  +0.57
delta_ratio     +0.32  +0.21  +0.16  +0.08  -0.00  +0.03  +0.26  +0.23  +0.13  -0.02  +1.00  +0.18
```

RSI(7), RSI(14), RSI(21), Bollinger position, DI spread and distance-to-EMA20 are **one number
wearing six hats** (ρ 0.80–0.97). Showing all six is not confluence; it is the same vote counted
six times, which is exactly how a confluence score manufactures confidence it does not have.

The one column that stands apart is **`delta_ratio`** (ρ 0.03–0.32 with everything else).
Approximated order flow is the only axis here genuinely orthogonal to price transforms — even
though its own IC is ~0. That is the real argument for building the order-flow layer, and it is a
better one than "footprints look professional".

### 2.2 The calls

| Item | Call | Why |
| --- | --- | --- |
| `ema`, `atr` (Wilder), `bollinger` | **KEEP** | ATR normalises every tolerance and stop in the system; the bands define the squeeze |
| `rsi(14)` | **KEEP one** | as the single oscillator. Drop 7 and 21 — ρ 0.92 / 0.97 with it |
| `rsi(7)`, `rsi(21)`, `bb_pos` as a reading, `di_spread` as a direction reading | **MERGE** | all ρ ≥ 0.80 with rsi14 / dist_ema20. Collapse to **one "stretch" number** = distance from EMA20 in ATRs. One honest axis instead of six correlated badges |
| `adx` | **KEEP, reclassify** | IC ≈ 0 for direction — correct, it has no sign. It is a **regime gate**, and it is the regime split the counted base rates need. Move it out of the direction readings |
| `macd` line + histogram | **MERGE into stretch** | ρ 0.82 with rsi14; `macd_hist` alone is −0.02, under the noise floor |
| `obv` | **REMOVE** | ρ 0.5+ with `cvd_slope`, IC −0.03/−0.01, and CVD is the better-defined version of the same idea. Two cumulative volume lines on one screen is clutter |
| `supertrend` | **KEEP** | `mother_candle` sets its own stop from it; `is_default_sl_tp=False` depends on it |
| `volume_z_1h`, `vol_ratio20` | **KEEP, demote** | IC ≈ 0 — volume cannot say direction. A **confirmation gate** on a break, never a vote |
| `structure.py` entire module | **KEEP** | fresh-cross BOS, CHoCH, FVG, squeeze, displacement. Best code in the repo |
| `reading.volume_profile`, `vwap_block` | **KEEP, REWRITE storage** | arithmetic is right; recomputed on every page load from 2 days of 15m bars and never stored. Needs 1m precision and a `profiles` table |
| `reading.barrier_outcome` | **KEEP, promote** | this *is* the triple barrier the brief asks for, already with the right definition |
| `outlook.counted_direction` + `MIN_SAMPLE` | **KEEP, protect** | the honesty floor. Everything new routes through it |
| `insights` ~15 rules | **KEEP, instrument** | each already writes a `predictions` row. Put the per-rule hit rate vs baseline on the screen |
| `market_snapshots` 26 columns | **REWRITE** | written per analyst cycle (5 min), 15m only. A base-rate table must be per-bar per-timeframe |
| `analytics.py` | **RENAME → `journal.py`** | it is the Journal/email day report. The single most confusing name in the package for anyone arriving at "the analytics part" |
| Stochastic, CCI, Williams %R | **not present — nothing to remove** | the brief assumed they were here. They are not. Do not add them |

Net effect: the indicator surface **shrinks** (one oscillator, one stretch axis, ADX reclassified,
OBV gone) while the *data* surface grows (1m base, real flow, stored zones). The brief's instinct
that redundant oscillators needed culling was right — there were just fewer of them than expected,
and they were hiding as six different names for one measurement.

---

## 3. Where the brief and the repo disagree

Six real conflicts. Each has a recommendation; §9 is where you overrule me.

| # | Brief says | Repo is | Recommendation |
| --- | --- | --- | --- |
| 1 | "pure, **vectorised (pandas/numpy)**" | zero numpy in runtime deps; `indicators.py` is pure-Python lists; `CLAUDE.md`: *"Add a dependency only with a runtime owner"* | **Split it.** Live stays pure-Python in `tradebuddy/`. A new offline `research/` extra gets pandas/numpy/numba for backtests, labelling and weight fitting, and never enters the Docker image. Pay for it with the **parity test**: offline and live agree to 1e-9 over 5,000 bars, per indicator. Train on one definition and trade on another and every backtest number in the system is fiction |
| 2 | "single source of truth: store 1m, derive everything by resampling" | **only 15m is stored**; the feed subscribes only to resolutions a strategy declares; `structure.resample(factor)` already exists | **Adopt it, in two steps.** 1m is the right base and is what makes sessions, sweeps and an honest volume profile possible. But 1m × 3 symbols × 1 year is 1.6M rows / ~130 MB, so 1m gets **90-day retention** and 15m-and-above stays forever. Backfill from `history_range` (it supports 1m) before switching the feed |
| 3 | "TradingView **Lightweight Charts** + Plotly" | hand-rolled SVG, theme-safe; the deleted plan explicitly argued against a chart library | **Overrule the old plan — take Lightweight Charts, for the price pane only.** The old argument held when the ask was sparklines. A terminal needs pan/zoom/crosshair over 50k candles with a dozen overlays; reimplementing that in SVG is weeks and will be worse. ~45 KB, MIT — **vendor it into `static/js/vendor/`, do not add a third CDN.** Everything that is not a price pane stays on `chart.js`. No Plotly |
| 4 | "BTC, ETH, **SOL** and more" | `UNDERLYINGS = {"BTCUSD": "BTC", "ETHUSD": "ETH"}`, hardwired in `options.py` and read by six modules | **Make the symbol set a setting**, and accept that SOL will have **no options data** unless Delta lists a SOL chain. Panels must degrade to an explicit "no chain for this symbol" state, never a blank box |
| 5 | "real order flow: trades + L2, footprint, DOM, heatmap" | the feed subscribes to `v2/ticker` and `candlestick_*` only — **no `all_trades`, no `l2_orderbook`** | **Verify the Delta API first, then build, and store aggregates not raw.** The raw tape is the one series that can actually hurt this database. Keep raw trades 7 days, write **1-minute footprint bins** (price bin × buy/sell size) long-term, and fold a **1 Hz top-20 book** into a 1-minute heatmap grid — never the raw book. Until it exists, the Order Flow screen ships approx-only with an empty state naming exactly what is missing |
| 6 | "Confluence **score 0–100** per direction, weighted factors" | `outlook.py` refuses a percentage under `MIN_SAMPLE=200`; `verdict.score` refuses under `MIN_MARKED=30`; `provenance.py` badges where every number came from | **Build it, but subordinate it to the existing honesty rules.** §2.1 says no factor has direction skill, so a weighted sum of them is a confident-looking number with ~0 information. The score renders **only** beside its counted hit rate, its baseline and its `n`, and its factor list must be **de-correlated first** (§2.2) or it counts one vote six times. A 0–100 dial with no record under it is precisely the thing this codebase spent three phases removing |

---

## 4. The terminal

### 4.1 What "Bloomberg-like" actually means

Not "more charts". Four properties, in order of how much they matter:

1. **One screen, many panels, no scrolling for the decision.** Today's Market page is a 64 KB
   vertical scroll. A terminal is a tiled grid where the answer and its evidence are visible at once.
2. **A command line.** `BTC <GO>`, `ETH FLOW <GO>`, `BTC OMON <GO>`. Function mnemonics are *the*
   reason a Bloomberg user is fast: no mouse, no menu tree, muscle memory.
3. **Density with discipline.** Monospace tabular numerals, aligned columns, colour meaning state
   and nothing else. Every number carries its provenance badge — you already have this, and it is
   the single most Bloomberg-like thing in the repo.
4. **Every number links to how it was made.** Click a figure → the inputs, the window, the sample
   size, the record of that card. `verdict.py` + `predictions` already make this possible.

### 4.2 The screen

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│ BTCUSD  109,842.5  ▲+1.24%   mark 109,844  idx 109,831  basis +0.012%  fund +0.0091%/8h     │ ticker rail
│ ATR 0.42%  IV 48.2  IV/RV 1.14  OI 1.42B ▲3.1%  24h 109,120–110,870   ⚠ feed 2s   PAPER     │
├──────────────┬──────────────────────────────────────────────────────┬───────────────────────┤
│ ① CONTEXT    │ ③ PRICE  15m ▾   [STRU][ZONE][LIQ][VWAP][PROF][SIG]  │ ⑥ LEVELS IN PLAY      │
│              │                                                      │                       │
│ 1d  ▲ up     │        ╱╲        ┄┄┄ call wall 112,000               │ 112,000  call wall    │
│ 4h  ▲ up     │     ╱╲╱  ╲   ▓▓  ← OB (fresh)                        │ 110,870  PDH          │
│ 1h  ▬ range  │    ╱      ╲╱╲    ░░ FVG open                         │ 110,410  VAH          │
│ 15m ▼ down   │  ╱          ╲ ← BOS↓  ✱ sweep                        │ 109,955  POC     ●    │
│ 5m  ▼ down   │                                                      │ 109,640  VAL          │
│ 1m  ▬ range  │  ├─ vol ▁▃▂█▅▂▁                                      │ 109,120  PDL          │
│              │  ├─ Δ   ▁▂▁▅▃▁▁  APPROX                              │ 108,500  put wall     │
│ aligned 4/6  │  └─ CVD ╱╲╱                        [profile ▶]       │                       │
│ conflict 15m │                                                      │ ⑦ SESSIONS (UTC)      │
├──────────────┼──────────────────────────────────────────────────────┤ ASIA 109,2–109,9      │
│ ② READING    │ ④ SIGNALS                                            │ LDN  109,6–110,8 ✓    │
│              │ ─────────────────────────────────────────────────    │ NY   open in 02:14    │
│ 4h stretch   │ 14:45  BTC  LONG  61  entry 109,840  sl 109,310      ├───────────────────────┤
│ +1.8 ATR     │        tgt 110,870 / 112,000   RR 1:1.94   PENDING   │ ⑧ RECORD              │
│ over EMA20   │        ▸ HTF up · discount · OB fresh · sweep ✓      │ outlook   54% n=412   │
│              │          · LTF CHoCH ✓ · CVD ✓ · vol ok              │           base 52%    │
│ counted:     │ ─────────────────────────────────────────────────    │ levels    61% n=88    │
│ 47% up in 4h │ 13:30  ETH  SHORT 48  …  EXPIRED (no trigger)        │ shapes     —  n=14    │
│ base 51%     │                                                      │ pcr       58% n=71    │
│ n=2,180      │ ⑤ TICKET            size 0.08  risk 0.75% = 7.50 USD │ ───────────────────── │
│ COUNTED      │ [ BUY 109,840 ]  [ auto: tb_master ▾  OFF ]  PAPER   │ 30d  +2.1R  34 trades │
└──────────────┴──────────────────────────────────────────────────────┴───────────────────────┘
  cmd ▸ BTC FLOW _                      MKT STRU FLOW PROF OMON VOL SCOR SIG TKT REPL STAT SYS
```

### 4.3 Functions

The command bar resolves `<SYMBOL> <FUNCTION> <GO>`. Each function is a layout over panels that
mostly already exist — a router and a grid, not twelve new pages.

| Mnemonic | Screen | Core panels | Exists? |
| --- | --- | --- | --- |
| `MKT` | Market monitor (home, above) | ①–⑧ | **yes** — reassembled from `market.html` |
| `STRU` | Structure: swings, BOS/CHoCH log per TF, zones with lifecycle, premium/discount | ③ full-width + break log | logic yes, **lifecycle storage no** |
| `FLOW` | Order flow: delta, CVD, divergence; footprint/DOM/heatmap when real data exists | new | **approx yes, real no** |
| `PROF` | Volume profile: session / prev / composite, POC-VAH-VAL, naked POCs, HVN/LVN, TPO | ⑥ full-width | arithmetic yes, **needs 1m + storage** |
| `OMON` | Option monitor: chain, OI by strike, IV smile, term curve, skew, max pain, walls | from `market.html` | **yes** |
| `VOL` | Volatility: ATR / RV / IV cones, IV-RV spread, regime percentile, implied-vs-realised record | partial | partial |
| `SCOR` | Scorecard: per-card hit rate vs baseline, calibration plot, per-factor performance | ⑧ full-width | `verdict.score` yes, **calibration no** |
| `SIG` | Signal blotter: live + history, outcomes, MFE/MAE, bars to resolution | ④ full | **no — `signals` table doesn't exist** |
| `TKT` | Trade ticket from a signal → existing risk manager → paper/live | ⑤ | `order_ticket` exists, **not signal-driven** |
| `REPL` | Replay: step a historical day bar by bar through the same pure functions | new | **no, but cheap** |
| `STAT` | Numeric tables, any series, sortable, CSV/Parquet export | new | **no** |
| `SYS` | processes, jobs, DB, event rates | `system.html` | **yes** |

`REPL` deserves a note. Because `structure`, `reading`, `outlook` and `verdict` are pure and take
plain lists, replay is "slice the bars at bar *t*, call the same functions". It is the cheapest
high-value screen on the list, it is a **look-ahead test you can see with your own eyes**, and it
is how you would actually practise reading the market by hand.

### 4.4 Rules the screen must obey

- **No panel invents a number.** Every panel calls the same pure function the engine and a worker
  call. One definition, three consumers — the `tb_master.decide()` discipline.
- **Every figure keeps its provenance badge**, plus a new **APPROX** marker for anything derived
  from OHLCV rather than tick data (delta, CVD, uniform-distributed profile).
- **A probability without `n` and a baseline is not rendered.** Existing invariant; now applies to
  the confluence score too.
- **A number lives on one screen.** `CLAUDE.md` forbids the Overview duplicate. The terminal does
  not break this: panels are *composed from one block definition*, not copied — one `profile` panel
  used by both `MKT` and `PROF`, never two implementations.
- **Degrade loudly.** No chain for SOL, no tape yet, no 1m before the backfill — each is an
  explicit panel state naming what is missing and the command that fixes it.
- **Dark, monospace tabular numerals, colour only for state**, and a shape or label on every series
  — red/green alone fails for a colour-blind reader and these are money decisions.
- **5,000+ candles must pan smoothly**: Lightweight Charts for the price pane, overlays lazy-loaded
  per toggle, everything else downsampled server-side.

---

## 5. The data inventory — kya kya data chahiye

This is what the screen and the brief both stand on. **Have** = in the repo today.

### 5.1 Raw market data

| Data | Have | Source | Needed for | New work | Cost/day/symbol |
| --- | --- | --- | --- | --- | --- |
| **1m OHLCV** | ✗ | `candlestick_1m` WS + `history_range` | base for every TF, sessions, precise profile, sweeps | feed subscription + backfill (`bars` is already keyed by interval) | 1,440 rows ≈ **115 KB** |
| 5m / 15m / 1h / 4h / 1d | 15m only | resample from 1m (`structure.resample` exists) | MTF context | resampler + cache | derived, ~0 |
| mark / index / basis | live only | `v2/ticker` | basis panel, funding arb | store per bar | 3 cols on `bars` |
| open interest per bar | ✓ | `OI:<symbol>` | OI × price matrix, positioning | — | — |
| funding paid per bar | ✓ | `FUNDING:<symbol>` | carry, crowding | — | — |
| **predicted funding + next funding time** | ✗ | ticker fields | countdown, pre-funding fade | parse + store | tiny |
| 24h turnover / volume / OI-USD | live only | `v2/ticker` stats | liquidity context | store per bar | 3 cols |
| **trades tape** | ✗ | `all_trades` WS — **VERIFY** | real delta, footprint, absorption, time & sales | new subscriber + writer | ~100k trades ≈ **6 MB raw** → keep 7d |
| **L2 orderbook** | ✗ | `l2_orderbook` WS — **VERIFY** | DOM ladder, liquidity heatmap, book imbalance | new subscriber, **aggregate not raw** | top-20 @1Hz → 1-min grid ≈ **1.2 MB** |
| **liquidations** | ✗ | VERIFY if published | sweep context — the best available explanation of a wick | subscriber | small |
| option chain per strike | summary only | `v2/ticker` on `C-`/`P-` | skew surface history, OI migration by strike | widen `options_history` or new `option_chain` table | ~400 strikes × 15m ≈ **1.5 MB** |
| prev day / week / month OHLC | ✗ | derived from 1m | reference levels everyone watches | pure fn + cache | ~0 |
| session H/L/open (Asia 00-08, LDN 07-16, NY 13-22 UTC) | ✗ | derived from 1m | session levels, initial balance, session VWAP | pure fn + cache | ~0 |
| economic / event calendar | ✗ | external | event risk on the vol panel | **out of scope** — flag it, don't half-build it | — |
| cross-asset (DXY, SPX, gold) | ✗ | external | macro context | **out of scope** | — |

### 5.2 Derived — and whether it survives a restart

Today almost all of this is recomputed on every page load and never stored. Which means no base
rate can be counted on it. Which means the screen can never say how often it worked.

| Derived series | Computed | Stored | Needs |
| --- | --- | --- | --- |
| swings / BOS / CHoCH | ✓ `structure.py` | ✗ | **`structure_events`** — kind, direction, level, displacement, volume ratio, TF, bar; settled with follow-through vs baseline |
| FVG | ✓ | ✗ | **`zones`** — created_at, last_touched_at, state (open / partial / filled), TF, strength |
| order blocks | ✗ | ✗ | detector (last opposite candle before a ≥1.5×ATR displacement that caused a BOS) → same `zones` table |
| equal highs/lows, liquidity pools | ✗ | ✗ | detector (swings within 0.1×ATR) + **`liquidity_pools`** with touch count |
| **sweeps** | ✗ | ✗ | detector (trades beyond, closes back inside) + reaction after N bars. **The one pattern most worth its own hit rate** |
| S/R zones (clustered, not lines) | partial `levels_block` | ✗ | clustering → `zones` |
| VWAP + σ bands, anchored VWAP | ✓ `vwap_block` | ✗ | session reset at 00:00 UTC; user anchors stored |
| volume profile POC/VAH/VAL | ✓ `volume_profile` | ✗ | **`profiles`** per session / day / composite; HVN/LVN; naked POC tracking; 1m-precision distribution |
| TPO / market profile | ✗ | ✗ | optional module: letters, initial balance, single prints, poor high/low |
| approx delta / CVD | ✗ (audit script only) | ✗ | pure fn + per-bar column, `approx=True` |
| real delta / footprint / imbalance / absorption | ✗ | ✗ | needs the tape (§5.1) |
| ATR, RV, vol regime percentile | ✓ | ✗ | per-bar column |
| per-bar indicator values, per TF | ✓ (recomputed) | ✗ | **`features`** — what `market_snapshots` was reaching for, but per-bar and per-timeframe |
| MTF context object | ✓ `outlook` + `indicator_agreement` | memory cache only | **`context_snapshots`** per bar |
| confluence score + factors | ✗ | ✗ | **`signals.factors`** JSON + the score's own `predictions` row |
| signals with outcome (MFE / MAE / time-to-resolution) | ✗ | ✗ | **`signals`** — the biggest single gap for the `SIG` screen |
| card verdicts + settlement | ✓ | ✓ `predictions` | extend `source` |

### 5.3 Storage arithmetic, honestly

2 symbols today; plan for 3. Current database is 9.6 MB.

| Table | Rows/day (3 sym) | Bytes/row | Per day | Retention | Steady state |
| --- | ---: | ---: | ---: | --- | ---: |
| `bars` 1m | 4,320 | ~90 | 0.4 MB | **90 d** | **35 MB** |
| `bars` 15m+ | 480 | ~90 | 43 KB | forever | 16 MB/yr |
| `features` (per bar × 6 TF × ~30 cols) | ~8,000 | ~260 | 2.1 MB | 1m/5m 90 d, rest forever | ~190 MB/yr ⚠ |
| `zones` | ~150 | ~160 | 24 KB | closed + 90 d | ~3 MB |
| `structure_events` | ~200 | ~150 | 30 KB | 365 d | ~11 MB |
| `liquidity_pools` | ~60 | ~140 | 8 KB | 365 d | ~3 MB |
| `profiles` | ~12 | ~2 KB | 24 KB | 365 d | ~9 MB |
| `signals` | ~40 | ~700 | 28 KB | forever | ~10 MB/yr |
| `option_chain` | 4,600 | ~120 | 0.6 MB | 30 d / 15m 1 yr | ~17 MB |
| `trades` raw | ~300k | ~60 | **18 MB** | **7 d** | **126 MB** ⚠ |
| `footprint` 1m bins | ~130k | ~40 | 5.2 MB | 90 d | **470 MB** ⚠⚠ |
| `book_heatmap` 1-min grid | 4,320 | ~900 | 3.9 MB | 30 d | **117 MB** ⚠ |
| existing tables | | | | | ~75 MB |
| **total without order flow** | | | **~3 MB** | | **≈ 290 MB at 1 year** |
| **total with order flow** | | | **~30 MB** | | **≈ 1.0 GB at 1 year** ⚠ |

Three conclusions that change the plan:

- **Everything except order flow is cheap.** ~290 MB at a year is a non-issue.
- **`features` is the sleeper** — 190 MB/yr, because it is rows × timeframes × columns.
  Mitigation: features for **15m and above forever, 1m/5m for 90 days**, and hold the column list
  to the de-correlated set from §2.2. That is the second reason to do the cleanup first.
- **Order flow triples the database and is the only real cost.** Hence raw tape 7 days, footprint
  as 1-minute bins not per-trade, book as a heatmap grid not snapshots — and **SQLite stops being
  obviously right at that point.**

### 5.4 SQLite, Parquet or Postgres — the brief asks, here is the answer

**Stay on SQLite for everything except the order-flow tier. Add Parquet as an export format, not
as a second live store.**

- SQLite handles 300 MB and millions of rows without complaint, and the repo's whole correctness
  story — one writer (`System`), parameterised SQL, retention in `housekeeping`, `TABLE_INFO` —
  is built on it. A second live store means two retention policies, two backup stories, and a new
  class of "which one is right" bug.
- Parquet's win is columnar scans over long history. That is a *research* workload, which belongs
  in the offline `research/` extra (§3 #1) and reads an export, not a live writer.
- **Postgres/Timescale becomes right only if real order flow ships.** A year of footprint + DOM
  heatmap is the moment to move that tier — and only that tier — off SQLite. Decide at M8, not now.
- Export endpoints (CSV/Parquet for any symbol/TF/range) ship regardless: one endpoint, and it
  makes every number in §5.2 checkable in a notebook.

---

## 6. Target architecture, mapped onto `v3`

Not `app/analytics/`. The package already has the right shape; it needs four new pure modules, a
storage tier, and a screen layer.

```
tradebuddy/
  structure.py      KEEP    swings, BOS, CHoCH, FVG, squeeze, resample, shapes
  zones.py          NEW     order blocks, S/R clustering, zone lifecycle state machine
  liquidity.py      NEW     equal H/L, pools, sweeps, session + prev-day/week/month levels
  profile.py        NEW     volume profile (1m precision), POC/VAH/VAL, HVN/LVN, naked POC, TPO
  orderflow.py      NEW     approx delta + CVD + divergence; real footprint in phase 2
  reading.py        KEEP    tab blocks; volume_profile moves out to profile.py
  outlook.py        KEEP    MTF trend, √t arithmetic, counted direction   ← the honesty floor
  insights.py       KEEP    rules over market_context
  verdict.py        KEEP    one checkable claim per card
  confluence.py     NEW     pure score() over de-correlated factors + explanation
  signals.py        NEW     signal lifecycle: pending → triggered → resolved; MFE/MAE/outcome
  provenance.py     KEEP    + APPROX source
  journal.py        RENAME  from analytics.py
  strategies/
    indicators.py   KEEP    pure-python, the authoritative definitions
  store.py          EXTEND  zones, liquidity_pools, structure_events, profiles, features, signals,
                            option_chain, trades, footprint, book_heatmap — each with retention
  system.py         EXTEND  one job per timeframe close; context cache per (symbol, tf, bar)
  api.py            EXTEND  the /api/analytics/* surface from the brief, as METHODS entries
  app.py            EXTEND  terminal routes + command resolution
  templates/
    terminal.html   NEW     the grid + command bar
    panels/*.html   NEW     one partial per panel, composed by every function screen
  static/js/
    vendor/lightweight-charts.js   NEW, vendored, no CDN
    panels/*.js     NEW     one module per panel
    chart.js        KEEP    everything that is not a price pane
research/           NEW, offline extra (pandas/numpy/numba): backtest, labelling, feature studies,
                    weight fitting. Never in the Docker image. Parity-tested against indicators.py
```

**Scheduling.** The brief asks for one Celery task per timeframe close with a Redis lock. The repo
already has a stronger guarantee: `CandleClosed` → `StrategyRunner` → `evaluator.submit`,
idempotent on `client_order_id`, results dropped when late (invariant 11). Analytics recomputation
should ride the same path — **one job per `(symbol, timeframe)` on bar close, computed once by the
engine, cached, served to every panel** — rather than a parallel Celery schedule with its own
locking. One fewer thing to keep in step.

---

## 7. Milestones

Each is one PR: tests green, `ruff` clean, `docker compose up --build` works, and a note saying
what changed / how to run it / what is tested / what is still missing.

| M | What | Why there | Size |
| --- | --- | --- | --- |
| **M0** | ✅ **done** — `analytics.py` → `journal.py` (and `collect_daily_analytics` → `collect_day`); audit committed | the name was the first thing that confused anyone touching "the analytics part" | 0.5 d |
| **M1** | ✅ **done** — `resample.py`, 1m storage + 90-day retention, a minute-cadence engine job, resumable chunked backfill, derived serving on `/api/bars`, 47 tests | everything downstream is wrong without a correct base | 2 d |
| **M2** | Indicator cleanup per §2.2 (merge six correlated readings into one stretch axis, drop OBV + rsi7/21, reclassify ADX as regime), migrate the strategies that read them, `features` table per bar per TF | shrink the surface *before* building on it | 2 d |
| **M3** | ✅ **done** — `liquidity.py` + `zones.py`: pivots, pools, sweeps with reaction, sessions, prev day/week/month, order blocks, FVGs, S/R bands, forward-only lifecycle, every rate against a baseline | the core of reading the market by hand | 3 d |
| **M4** | ✅ **done** — `profile.py` (POC/VA/HVN/LVN, naked POCs, TPO with IB, single prints, poor highs) and `orderflow.py` (delta, CVD, divergence, absorption, all APPROX) | | 2.5 d |
| **M5** | `confluence.py` + `signals.py`: score with explanation over **de-correlated** factors, signal lifecycle, MFE/MAE/outcome, per-factor performance, wired into `predictions` so the score is scored like everything else | | 2.5 d |
| **M5/M6** | *next* — `confluence.py`, `signals.py`, then `/api/analytics/*` + the terminal shell: grid, command bar, function router, Lightweight Charts price pane with overlay toggles; `MKT` `STRU` `PROF` `OMON` `SCOR` | first visible payoff | 4 d |
| **M7** | `SIG` blotter + `TKT` from a signal into the existing risk manager; one analytics strategy (**paper only, shadow mode first**); settings for weights and toggles; `REPL` | | 3 d |
| **M8** | **Real order flow, only after the API check in §9**: tape + book ingestion, footprint, imbalance, absorption, heatmap, `FLOW`; storage-tier decision | highest cost, highest uncertainty, nothing depends on it | 4 d + unknown |

Shortest path to something visibly better: **M0 → M1 → M2 → M6.** That gives the terminal over
correct data with a smaller, honest indicator set, while the zone / liquidity / signal layers land
behind it.

---

## 8. Risks

| Risk | Where | Mitigation |
| --- | --- | --- |
| **Look-ahead** in any new detector | zones, sweeps, profile, MTF resample | mandatory: compute to bar *t*, then to *t+k*; every value ≤ *t* identical. Plus `outlook.py`'s existing `shift(1)` discipline on resampled frames |
| **Training/serving skew** | `research/` pandas vs live pure-python | parity test to 1e-9 over 5,000 bars, per indicator. Non-negotiable — this failure mode is silent by nature |
| **The confluence score invents confidence** | `confluence.py` | de-correlate factors first (§2.2); never render without counted rate + baseline + `n`; the score gets its own `predictions` row |
| **The 4h mean-reversion finding** (§2.1) | both live breakout strategies | measure it in `research/` before M7 ships any new auto strategy. If it holds, the existing strategies need re-examining — a far more valuable outcome than a new screen |
| **1m storage and SQLite write load** | `bars`, `features` | 90-day 1m retention; one writer; `INSERT OR IGNORE`; incremental vacuum in `housekeeping` |
| **Order flow triples the database** | `trades`, `footprint`, `book_heatmap` | aggregate-not-raw; 7-day tape; tier decision deferred to M8 |
| **VM memory** — the deleted plan recorded 229 Mi free with the worker at 105 MiB | a new ingestion process | **measure before M8.** No pandas in the image. If the tape process doesn't fit, it doesn't ship |
| **Delta may not publish trades / L2 / liquidations** | M8 entirely | verify first; `FLOW` ships approx-only with an honest empty state regardless |
| **`CLAUDE.md` drift** | happened once already | every milestone that changes behaviour updates it in the same PR |
| **Live-trading regression** | `trading.py`, `guard.py`, brokers | analytics writes no orders, ever. New strategies are `Strategy` subclasses behind the existing `Trader` gate, paper-only, shadow-first |

---

## 9. Decisions — settled 21 Oct 2026

All four confirmed. These are now assumptions the milestones rest on, not open questions.

| # | Decision | Chosen | Consequence |
| --- | --- | --- | --- |
| 1 | Base timeframe | **1m base** | Shipped in M1 — with one deviation from the brief, below. 1m kept 90 days (~35 MB), 15m and above forever. Sessions, sweeps and a precise volume profile become possible |

**M1 deviation, worth knowing.** The brief and §6 said the *feed* would subscribe to
`candlestick_1m`. It does not. The engine collects the base series on its own 60-second job
instead, because a `CandleClosed` per symbol per minute would add ~2,880 event-log rows a day and
wake every strategy subscriber for a bar no strategy wants — and the feed's subscription set is
derived from what the strategies declare, so adding 1m there would have meant inventing a strategy
that does not trade. A REST read of the last few minutes is idempotent against the same table, costs
one call a symbol a minute, and leaves the feed exactly as the strategies define it. The live price
on the terminal still comes from the WebSocket tick, which is sub-second; only the *stored* minute
bar is up to a minute late, and nothing reads it in real time.
| 2 | Price chart | **Vendor Lightweight Charts** | ~45 KB MIT into `static/js/vendor/`, **no third CDN**. Price pane only; `chart.js` keeps every other visual. Overlay toggles lazy-load |
| 3 | Order flow | **Approx now, real at M8** | `orderflow.py` ships delta/CVD/divergence from OHLCV, badged APPROX. Delta's `all_trades` / `l2_orderbook` / liquidations availability verified in parallel; `FLOW` ships with an explicit empty state for footprint and DOM. Storage tier stays SQLite until that check returns |
| 4 | Symbols | **BTC + ETH** | No SOL for now. The symbol set still becomes a setting in M2, so adding one later is config rather than code, and `UNDERLYINGS` stops being hardwired across six modules |

Still open, but not blocking: whether the 4h mean-reversion in §2.1 survives a proper backtest.
That gets answered in `research/` before M7, because it decides whether the two live breakout
strategies are trading the right sign.

---

## 10. Invariants this plan would add

In the spirit of `CLAUDE.md` — each should fail the build.

24. **One base, every frame derived.** 1m is the only stored raw series below 15m; every other
    timeframe is resampled UTC-aligned from it. Nothing fetches a timeframe separately.
25. **No value changes after its bar closes.** Computing to bar *t* and to bar *t+k* gives
    identical values for every bar ≤ *t*. Asserted per indicator and per detector.
26. **One definition per indicator.** Offline (`research/`, pandas) and live
    (`strategies/indicators.py`, pure python) agree to 1e-9 over 5,000 bars.
27. **Estimated is labelled.** Anything derived from OHLCV rather than tick data carries
    `approx=True` and renders with the APPROX badge. A footprint built from candles is not a
    footprint.
28. **A score is never shown without its record.** The confluence score renders only beside its
    counted hit rate, its baseline and its sample size — the floor `outlook.MIN_SAMPLE` and
    `verdict.MIN_MARKED` already enforce.
29. **A factor list is de-correlated.** No two factors in the confluence score with |ρ| > 0.8 over
    the stored bars. Six correlated readings are one vote, not six.
30. **A zone has a lifecycle and it only moves forward.** fresh → tested → mitigated → invalidated;
    never backwards, always persisted with timestamps.
31. **Every panel calls the engine's function.** No panel computes its own version of a number the
    engine already publishes.
