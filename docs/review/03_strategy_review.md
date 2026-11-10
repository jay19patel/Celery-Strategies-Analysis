# 03 — Quant / Strategy Review

Scope: every strategy that can open a position, read from the code on branch `trading_system` (2026-11-07).
Evidence used: the code, the bundled backtester (`app/backtest/perp.py`) run on a **copy** of the live
database, the live trade table, and published research. Nothing here is a claim of profitability.

## 0. Evidence available — read this first

| Source | What it covers | Size |
|---|---|---|
| Closed trades, all brokers | `structure` table | **47** (10 auto, 37 manual) — auto P&L **−55.63 USD** |
| 15m perpetual bars | BTC, ETH (XAUT from Apr 2026) | 1 year (2025-11-01 → 2026-11-07) |
| 1m perpetual bars | BTC, ETH, XAUT | ~36 days |
| Recorded option chains (`data/chains/`) | BTC, ETH, XAUT | **5 days** |
| Footprint minutes (`FlowBar`) | tape-based strategies | **3 days** |
| Options minute (`OptionsHistory`) | `pcr_options` | ~2 weeks, with gaps |

Consequence: only the four bar-only perpetual strategies can be backtested today. Nothing about the
nine option structures, the four tape strategies, `ema_vwap`, `pcr_options`, zero-hero or the decay
spread can be concluded from data. The live sample (10 auto trades) is noise.

### Backtest results (stored bars, cost 0.13 %/round trip, funding 0.01 %/8h, next-bar-open entry)

Command: `DB_PATH=<copy> python -m app.backtest.perp <strategy> --underlying <U>`

| Strategy | Sym | Trades | Win % | Exp. R | Total % | PF | MaxDD % | Walk-forward folds (exp. R) |
|---|---|---|---|---|---|---|---|---|
| supertrend | BTC | 378 | 38.6 | **−0.16** | −61.7 | 0.76 | 63.6 | −0.14 / −0.17 / −0.20 / −0.14 |
| supertrend | ETH | 404 | 35.9 | **−0.11** | −67.4 | 0.80 | 89.4 | −0.26 / −0.03 / −0.10 / −0.07 |
| ema_9_15 | BTC | 27 | 25.9 | **−0.43** | −5.4 | 0.57 | 5.4 | all 4 negative |
| ema_9_15 | ETH | 51 | 27.5 | **−0.43** | −11.0 | 0.58 | 11.5 | all 4 negative |
| trend_4h_short | BTC | 11 | 27.3 | +0.01 | −3.7 | 0.85 | 15.5 | too few trades |
| trend_4h_short | ETH | 9 | 33.3 | +0.32 | −0.6 | 0.98 | 19.5 | too few trades |
| trend_4h_long | BTC | 12 | 33.3 | +0.14 | −0.9 | 0.95 | 7.7 | too few trades |
| trend_4h_long | ETH | 12 | 16.7 | **−0.41** | −20.3 | 0.33 | 20.9 | too few trades |

Caveats of the harness itself: it does **not** model the live trailing stop (`TRAIL_*` in
`trading/service.py:55`), the day-bias gate, the "stopped out today" rule, or exchange-stop slippage
(stops fill exactly at the stop price). So backtest ≠ live, and the gap is unmeasured.

---

## 1. Directional strategies (perpetual futures) — `backend/app/strategies/`

All share: pure `decide()` on closed bars (the runner folds 1m into the timeframe and drops the
forming bar, `trading/runner.py:111-122`), a stop "where the idea is wrong", a target, sizing so the
loss at the stop is `risk_per_trade_pct` × edge scale (`service.py:493`), a mark-price guard exit,
a reduce-only stop-market on Delta (`market/delta.py:185`), a 1R trailing stop, a refusal against a
day bias ≥ 0.5, and no re-entry the same day after a stop.

### 1.1 `supertrend` — Supertrend(10,3) flip on 15m with EMA(50) filter
- **Does:** enters on the bar the Supertrend direction flips, if price is on the same side of EMA50; stop on the band, target 2R.
- **Edge sought:** short-term trend following. Literature on simple technical rules in crypto is mixed and weakest for Bitcoin out of sample (Hudson & Urquhart 2019; Li 2023 finds none in 2022-23).
- **Implementation:** correct (closed bars, no look-ahead found). On 15m the stop is ~0.3–1 %, so the 0.13 % round trip eats 15–40 % of each R.
- **Risk:** bounded by stop; whipsaw in ranges.
- **Verdict: REMOVE.** −0.11 to −0.16 R over ~400 trades per symbol, *every* walk-forward fold negative on both symbols. The settings file already admits it (`settings.py:72-74`) and still enables it by default.

### 1.2 `ema_9_15` — asymmetric 9/15 EMA cross on 5m
- **Does:** buy when the prior high was below EMA15 and the close is above it; sell when the prior close was above EMA9 and the close is below it. Stop beyond the signal candle, 2R target.
- **Edge sought:** none articulated; the buy/sell rules are not mirror images, which is a curve-fit smell.
- **Verdict: REMOVE.** −0.43 R on 27/51 trades (only 36 days of 1m data), every fold negative, admitted losing in `settings.py:72`.

### 1.3 `trend_4h_short` / `trend_4h_long` — Supertrend flip on 4h with EMA(200)
- **Does:** same flip logic on 4h; stop 2 ATR, target 5R, held up to 10 days. Two classes, one per side.
- **Edge sought:** medium-horizon time-series momentum — the best-supported idea in this repo (Moskowitz, Ooi & Pedersen 2012; Liu & Tsyvinski 2018 for crypto), but contested for crypto (Grobys & Sapkota 2019) and very sensitive to entry timing.
- **Implementation:** `MIN_BARS = 400` 4h bars needs ~67 days of warm-up. The docstring claims a two-year, 8-perpetual out-of-sample test; that data is **not in this repo** (only one year of BTC/ETH 15m), so the claim cannot be reproduced. The docstring also says the long is "off by default" — it is **on** by default (`settings.py:75` enables every directional strategy).
- **Risk:** 2-ATR stop on 4h ≈ 3–8 %; with `paper_leverage` 10 the margin is fine, but a weekend gap is unmodelled. Funding is charged once in `cost_pct`, while a 10-day hold pays it ~30 times.
- **Verdict: `trend_4h_short` KEEP WITH FIXES (paper only); `trend_4h_long` REMOVE from defaults.** ~10 trades a year per symbol is far too few to know anything for years. Merge into one class with a `sides` parameter.
- **Improvements:** (1) charge funding per 8h held, not once; (2) make the backtester apply the trailing stop so live and test match; (3) evaluate on a basket of perps the repo actually stores, or stop citing the 8-perp test.

### 1.4 `ema_vwap` — EMA(9) crosses UTC-session VWAP on 5m
- **Does:** fresh cross of session VWAP, agreeing with the 15m outlook, within 1 ATR of VWAP; stop beyond VWAP + 0.25 ATR; target the next level from `analysis/nearby`, ≥ 1.5:1 after costs.
- **Edge sought:** VWAP as institutional reference. Weak evidence in 24/7 crypto, where "session" is an arbitrary UTC midnight.
- **Implementation:** depends on `outlook` and `structure` (large heuristic modules), so it **cannot be backtested** by the harness (`perp.py:184`). Target quality depends on an unvalidated level-merging model.
- **Verdict: REWORK** — either make its inputs replayable from stored bars and backtest it, or remove it. No evidence either way today.

### 1.5 `pcr_options` — change in put/call open-interest ratio over 4h → trade the perp
- **Does:** if PCR(OI) rose ≥ 0.15 over 4h on this bar (latched to the crossing bar) → long; fell → short. Stop beyond the signal candle, 2R.
- **Edge sought:** "put writing = support". No peer-reviewed evidence found that PCR-OI *changes* predict BTC returns; public commentary reads PCR the opposite way (high PCR = bearish hedging). Delta India's option OI is a small, retail-heavy slice of a market whose price discovery is on Deribit/Binance/CME.
- **Implementation:** latch logic and as-of-time windowing are careful (`pcr_options.py:42-57`). Needs ≥ 4h of the engine's own recorded history, so it cannot be backtested.
- **Verdict: REMOVE.** Weak hypothesis, venue-specific thin data, untestable.

### 1.6–1.9 Auction / order-flow family — `strategies/auction.py`
| Strategy | Does | Main concern |
|---|---|---|
| `value_area_return` | Opened outside yesterday's value area, two 15m closes back inside → trade to the other edge ("80 % rule") | Market Profile folklore; no rigorous evidence; "yesterday" is the IST/UTC day on a 24/7 market |
| `sweep_absorption` | Liquidity sweep + tape absorption at the same price → reversal | Tape is Delta India's trades only, a minor venue |
| `exhaustion_fade` | Climax at a day level that fails → fade to POC | Same; plus GEX levels rest on a dealer-positioning assumption the code itself flags (`options/gex.py:5-9`) |
| `flow_breakout` | Trend day, close beyond value with stacked imbalances and delta → follow | Same |

- Research: order imbalance in Bitcoin is strongly *contemporaneous* with returns and has short-horizon predictive content at seconds-to-minutes; a stable tradable edge at 5–15m is not established.
- None can be backtested: 3 days of `FlowBar`, and the harness refuses `wants_flow`/`wants_bias`.
- **Verdict: REWORK (paper only).** Keep running on paper so `trading/edge.py` can measure them; after 30 trades each, delete any whose 95 % range is below zero (the code already zero-sizes it — delete the file too). Add a replay backtest over stored `FlowBar` before any capital.

---

## 2. Option structures (auto-trader) — `options/strategies.py`, `options/regime.py`, `options/assess.py`

**How it decides:** every `auto_interval_minutes` per underlying: nearest expiry in 12–60h → regime
(ATM IV vs max of four realised-vol measures; direction from hourly EMAs, 24h z-move, RSI; GEX sign)
→ a hand-weighted 0–100 score per structure → build strikes by |delta| 0.18 and 2 %-of-spot wings
→ checks (liquidity, credit/width, POP ≥ 55 %, **EV at realised vol after fees > 0**) → best by score
then edge → size so worst case = 2 % equity × edge scale × drawdown scale.

**Edge sought:** the variance risk premium — implied vol above realised. This is the best-documented
edge in the repo: Bitcoin's VRP is positive on average and larger than the S&P 500's, but regime
dependent and sometimes deeply negative (Alexander & Imeraj 2020; Almeida et al. 2024).

**Cross-cutting implementation issues**
1. **Fail-open on missing realised vol.** `assess.py:101-104` only *adds* the "edge at realised vol" check when an EV exists. With < 200 stored bars (fresh install, new underlying) the check is absent, so a required check silently disappears and the structure can pass. Fix: fail the check when `ev is None`.
2. **Lognormal tails.** POP/EV use a driftless lognormal (`pricing.py:74`). Crypto returns are fat-tailed with jumps, which biases EV in favour of sellers exactly where it hurts. Use the empirical distribution of stored 15m returns over the holding horizon.
3. **The score is not calibrated.** Weights (`+8` positive gamma, `−trend*35`, …) are hand-set; `stats()["by_score"]` exists to check them but has no data. Treat the score as a filter, not a ranking, until it is.
4. **Narrow wings, fee drag.** 2 % wings with 0.18-delta shorts on a 12–60h BTC expiry give credits the code itself puts at "6–10 % of width" (`settings.py:96-98`); four legs of fees (capped at 10 %×1.18 of premium each) take a large share.
5. **Single event date.** Only one hardcoded FOMC timestamp (`settings.py:103`). CPI, NFP and crypto-specific events (ETF flows, unlocks) are ignored.
6. **No portfolio vega/gamma cap.** All credit structures are short vol; three at once on BTC and ETH are one correlated bet.

| Structure | Risk | Verdict | Reason |
|---|---|---|---|
| `iron_condor` | Defined | **KEEP WITH FIXES** | Cleanest VRP harvest; held to settlement (`guard.py:116`) is defensible because worst case = sized loss |
| `bull_put_spread` / `bear_call_spread` | Defined | **KEEP WITH FIXES** | VRP plus a directional tilt; direction model is a heuristic |
| `iron_butterfly` | Defined | **KEEP WITH FIXES** | POP exempt by design; pin dependence; same VRP thesis |
| `short_strangle`, `short_straddle`, `short_call`, `short_put` | **Undefined** | **REMOVE from auto** | Refused on Delta live (`service.py:391`), so a paper edge here can never be deployed; contradicts "controlled drawdown"; short-gamma tail with a 5s in-process stop as the only protection |
| `long_straddle` | Defined debit | **REMOVE** | Buying vol against a positive VRP is negative carry on average; 4 live auto trades, all losers (−36.68); code notes nine re-buys in one morning (`autotrader.py:148`) |

---

## 3. Zero-hero buy and expiry-decay spread — `options/zerohero.py`, `options/decay.py`, `trading/zerohero.py`

### 3.1 `zero_hero` — OTM option bought 2–4h before daily expiry on a momentum break
- **Does:** five-gate scan (payoff multiple ≥ 6x at a fib-extension target, touch probability from realised vol, fresh structure break with displacement and flow, away from max pain, spread/OI/capacity). Budget 20 % of equity **per day**, 3 attempts.
- **Model flaw:** the EV formula assumes an exit at the target multiple (≥ 6x, `zerohero.py:257`), but the guard takes profit at `zh_take_profit_pct` = 300 % (= 4x). The EV that gates the trade does not describe the trade that is executed.
- **Risk:** near-certain loss of each premium; 20 %/day budget can lose 20 % of equity in a day, four times the daily loss limit's intent (the limit only halts after the fact).
- **Evidence:** 8 manual trades, 0 wins (−0.13). No literature supports a systematic edge in buying 0DTE OTM options against a positive VRP.
- **Verdict: REMOVE.** It also owns 19 of the 79 settings.

### 3.2 `expiry_decay` — sell a ≥ 1.5σ OTM option in the last hours, buy the next strike
- **Does:** defined-risk "picking up pennies" spread, EV at realised vol, held to settlement.
- **Coupling bug:** it only runs inside the zero-hero cycle (`trading/zerohero.py:192`), so it is gated by `zh_enabled` and the zero-hero window. Removing zero-hero removes it.
- **Verdict: REWORK** — decouple into its own strategy; paper only until measured. Thesis (VRP, theta in last hours) is plausible; lognormal tail problem applies doubly at 1.5σ.

---

## 4. Portfolio level

### 4.1 Position sizing — current and recommended
**Current:** fixed-fractional — loss at stop/worst case = `risk_per_trade_pct` (default **2 %**) of
equity, × a measured-edge multiple (`trading/edge.py`: 0 if proven losing over 30+ trades, ×0.5 if
losing but unclear, quarter-Kelly on the CI low end capped 1–2x if proven winning, 0 on live until
proven on paper) × drawdown scale (½ at −10 %, ¼ at −20 %). Unbounded structures are sized on 3
expected moves. This is a sound design — better than most retail bots.

**Recommendation for steady growth:** keep the scheme, change the numbers and add a portfolio cap.
1. `risk_per_trade_pct` default **0.5 %** (max 1 %) while every strategy is unproven. At 2 % and up to ~8 concurrent positions per broker, one correlated move can hit the 5 % daily limit in one go.
2. **Portfolio heat cap:** sum of open worst-case losses ≤ 4 % of equity per broker, all families combined.
3. Cap the Kelly multiple at 1x until a strategy has ≥ 100 trades; 30 trades is a thin basis for a bootstrap CI on fat-tailed R.
4. Volatility scaling is already implicit (stop distance from ATR); no extra layer needed.

### 4.2 Global risk controls
| Control | Exists? | Where | Gap |
|---|---|---|---|
| Max daily loss | Yes, 5 %/broker | `guard.py:187` | Start equity is taken at the first check of the day; a restart after losses resets the baseline |
| Max drawdown kill-switch | **No** (by design only scales size) | `edge.py:17-19` | Add a hard stop at e.g. −25 % from peak that needs a manual resume |
| Max open positions | Partial | `service.py:376`, `autotrader.py:99` | Counts **auto option structures only**; perps, zero-hero, decay and manual are outside it |
| Max exposure per asset | **No** | — | A broker can hold 1 option structure + 1 perp + 1 zero-hero + 1 decay on the same underlying, in opposing directions |
| Max leverage | Paper only | `settings.py:144` | Delta sizing/margin uses `paper_leverage` (`service.py:502,541`) whatever the exchange leverage is |
| Kill switch | Backend yes | `service.py:779` `close_all` | It closes but does not switch auto-trading off; see `05_ui_review.md` |

### 4.3 Overlap and conflict
- Perp strategies compete for one slot per symbol, best R:R wins (`runner.py:169`) — good.
- **No cross-family netting:** the options auto-trader can open a bull put spread while a perp short is open on the same underlying. Two separate direction models exist (`options/regime.direction` and `analysis/bias`) and can disagree.
- The four auction strategies, `ema_vwap` and `pcr_options` all trade the same perp on 5–15m: highly overlapping.
- All credit structures are one bet: short BTC/ETH vol. Their losses cluster on the same days.

### 4.4 What cannot be concluded
- Whether **any** strategy here makes money. The only statistically meaningful result is negative (Supertrend, 9/15 cross).
- Option strategy profitability: there is no historical option-chain backtest (5 days of recorded chains). The comment claiming hold-to-settlement replay results (`guard.py:113-115`) cannot be reproduced from the repo.
- Paper fill realism on Delta India options: paper fills at the touch up to shown size, but real IOC fills on thin books, queue position and partial fills are untested.
- The trend-4h out-of-sample claim (data not in repo).
- Tape strategies (3 days of tape).

**Minimum evidence before real money:** ≥ 30 paper trades per strategy with a 95 % CI above zero
(the code already enforces this on Delta live), plus ≥ 90 days of recorded chains replayed through
the option assess/exit logic.

---

## 5. Verdict table

| # | Strategy | Family | Verdict |
|---|---|---|---|
| 1 | supertrend | perp | **Remove** |
| 2 | ema_9_15 | perp | **Remove** |
| 3 | trend_4h_short | perp | Keep with fixes (paper) |
| 4 | trend_4h_long | perp | **Remove** (merge into one trend class if kept) |
| 5 | ema_vwap | perp | Rework |
| 6 | pcr_options | perp | **Remove** |
| 7 | value_area_return | perp | Rework (paper) |
| 8 | sweep_absorption | perp | Rework (paper) |
| 9 | exhaustion_fade | perp | Rework (paper) |
| 10 | flow_breakout | perp | Rework (paper) |
| 11 | iron_condor | options | Keep with fixes |
| 12 | bull_put_spread | options | Keep with fixes |
| 13 | bear_call_spread | options | Keep with fixes |
| 14 | iron_butterfly | options | Keep with fixes |
| 15 | short_strangle | options | **Remove** |
| 16 | short_straddle | options | **Remove** |
| 17 | short_call | options | **Remove** |
| 18 | short_put | options | **Remove** |
| 19 | long_straddle | options | **Remove** |
| 20 | zero_hero | options | **Remove** |
| 21 | expiry_decay | options | Rework (decouple) |

Net: 21 → at most 11, of which 4 have a documented edge thesis (VRP) and 1 (trend) a contested one.

## 6. Improvements ranked by expected impact
1. Delete the 10 "Remove" strategies and their settings (largest risk reduction, largest simplification).
2. Fail closed when realised vol is missing (`assess.py:101`).
3. Portfolio heat cap + per-underlying exposure cap across all families.
4. Default risk 0.5 %; hard max-drawdown stop.
5. Use the exchange's leverage, not `paper_leverage`, for Delta sizing/margin.
6. Replace lognormal POP/EV with the empirical return distribution from stored bars.
7. Event calendar beyond one FOMC date.
8. Make the backtester match live (trailing stop, funding per 8h, stop slippage) and add an options replay over `data/chains/`.

## Sources
- [Alexander & Imeraj — The Bitcoin VIX and its Variance Risk Premium](https://sro.sussex.ac.uk/id/eprint/91094/)
- [Almeida, Grith, Miftachov, Wang — Risk Premia in the Bitcoin Market](https://arxiv.org/html/2410.15195v2)
- [Hudson & Urquhart — Technical trading and cryptocurrencies (Annals of OR)](https://link.springer.com/article/10.1007/s10479-019-03357-1)
- [Li — technical strategies on BTC/ETH 2022-23, data-snooping adjusted](https://articlegateway.com/index.php/JAF/article/view/6453)
- [Borgards — momentum in cryptocurrencies (NAJEF 2021)](https://econpapers.repec.org/RePEc:eee:ecofin:v:57:y:2021:i:c:s1062940821000590)
- [Coinbase — trend following sensitivity to timing](https://www.coinbase.com/institutional/research-insights/research/monthly-outlook/trend-following-in-uncertain-times-september-2022)
- [Shen & Urquhart — Bitcoin intraday time-series momentum](https://centaur.reading.ac.uk/100181)
- [Order flow imbalance and bitcoin crashes (RQFA 2023)](https://link.springer.com/article/10.1007/s11156-023-01148-1)
- [Glassnode — BTC put/call ratios (OI vs volume)](https://studio.glassnode.com/charts/btc-options-putcall-ratios)
- [The Block — put/call ratio read as bearish hedging](https://www.theblock.co/post/274421/bitcoin-put-call-options-ratio-is-a-bearish-indicator-for-the-market-says-analyst)
- Moskowitz, Ooi & Pedersen (2012) "Time series momentum", JFE; Liu & Tsyvinski (2018) NBER w24877; Grobys & Sapkota (2019) Economics Letters — cited from the literature survey above.
