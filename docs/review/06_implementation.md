# 06 — Implementation (Phase 6)

What was built from the plan in `04_target_architecture.md`, what changed in behaviour, and what is
left. Nothing is committed: every change is in the working tree for review.

**Checks:** `make check` passes — ruff, eslint, tsc, **333 backend tests**, 8 frontend tests — and
`next build` compiles all 14 routes. The backend was also started on a copy of the live database:
settings migrated, every job ran without an error, an options cycle ran on paper.

## 1. Strategy plugins (the one-file goal)

- `backend/app/strategies/base.py`: `Strategy` (perpetual) and `OptionStrategy`, each with a
  Pydantic `Params`, a `version`, `default_on`.
- `backend/app/strategies/registry.py`: auto-discovery. Every module not starting with `_` is
  imported; every concrete subclass is registered by `name`. Two with one name refuse to start.
- One file per strategy: `ema_vwap`, `trend_4h_short`, `value_area_return`, `sweep_absorption`,
  `exhaustion_fade`, `flow_breakout`, `iron_condor`, `iron_butterfly`, `bull_put_spread`,
  `bear_call_spread`, `expiry_decay`. Shared helpers in `_auction.py`, `_options.py`.
- `CATALOG`, `BUILDERS`, `STRATEGY_KEYS`, the two Literals, the hand-written score block and the
  frontend strategy lists are gone. The Strategies page and the desk ticket draw their forms from
  each strategy's JSON schema.
- **Proof:** the template, copied under a new name, registered and traded with no other edit; a
  test asserts every registered strategy's on/off default and form.

## 2. Settings: 79 → 33

Global strategy lists and numbers became per-strategy `StrategyConfig` (enabled, live, underlyings,
params). Removed or made constant: `market_data`, `paper_slippage_pct`, `auto_options`,
`auto_interval_minutes` (15 min), `min_score` (60), `min_score_floor`, `event_buffer_hours` (2h),
`trade_margin_pct` (25%), `perp_max_hold_hours` (strategy param), `max_open_structures`,
`max_per_underlying`, the four exit percentages, `stop_confirm_checks` (3), `ai_brief_minutes`
(30), the three notify toggles (always on), and all 19 `zh_*`. Added `max_drawdown_pct`.
**Your stored values migrate on first start**: each strategy keeps its on/off, symbols, and the
option numbers you set (your `min_credit_width_pct` of 10 carried over).

## 3. Removed (the verdicts in `03_strategy_review.md`)

Supertrend, 9/15 EMA, 4h trend long, put/call shift, short strangle, short straddle, short call,
short put, long straddle, zero-hero (backend, page, 19 settings, window scheduler), and the code
only they used (`EventWindowTrigger`, `trading/timing.py`, `fibonacci.next_extension`,
`fibonacci.nearest`, the dead AI-review UI and type). The decay spread was decoupled from
zero-hero and is now an ordinary option strategy.

## 4. Risk and safety

| Change | Where | Test |
|---|---|---|
| Crash recovery of `opening`/`closing` structures | `TradingService.recover`, `Engine.start` | `test_safety` |
| One engine per database (file lock) | `Engine._take_lock` | `test_scheduler` |
| Stop all = every entry refused (persisted), then close all; Resume | `Engine.stop_all`, `risk.kill_state`, header | `test_api` |
| Drawdown stop (`max_drawdown_pct`, 25%) | `guard.check_daily_loss`, gate | `test_safety` |
| One automatic position per underlying, all families; heat cap 4% | `risk.portfolio_refusal` | `test_trading`, `test_autotrader` |
| Delta reconciliation every 5 min, blocks entries on mismatch | `trading/reconcile.py` | `test_safety` |
| Delta margin at the account's own leverage | `Broker.leverage` | `test_safety` |
| Missing realised vol fails the edge check | `options/assess.py` | `test_strategies` |
| Per-strategy "trade on Delta too" switch | `Settings.strategy_brokers` | `test_settings`, `test_autotrader` |
| Real money refused while the PIN is the default | `Engine.update_settings` | `test_safety` |
| Telegram token out of the logs | `main.py` | `test_safety` |
| Trailing stop re-reads the position under the lock; exits edit locked | `service.trail`, `update_exits` | existing |
| Manual job runs never overlap scheduled ones | `Scheduler` | `test_scheduler` |
| Tape judged against the mark (no lockout); flow ignores the forming bar and stale tape | `tape.py`, `MarketDesk.flow` | `test_safety` |
| Testnet data never stored as market history | `MarketFeed.record` | — |
| Funding charged for every payment a hold could meet | `runner._funding_pct` | `test_perp` |
| Login lockout per real client plus a global cap; PIN change ends other sessions; API docs behind the PIN | `auth.py`, `security.py`, `main.py` | `test_api` |
| Bounded Delta product cache; trendline requests capped and off the event loop | `delta.py`, `desk.py` | — |
| JSON logs in production; `strategy_version` on every structure | `main.py`, `models.py` | `test_api`, `test_autotrader` |

## 5. Backtester matches live

Next-bar entry as before, now with the live trailing stop, stop slippage (default 0.05%) and
`--params`. Re-run on stored bars with live exits:

| Strategy | Sym | Trades | Exp. R | Exits |
|---|---|---|---|---|
| trend_4h_short | BTC | 11 | **−0.48** | 6 stop, 5 trailing, 0 target |
| trend_4h_short | ETH | 9 | +0.23 | 4 stop, 5 trailing, 0 target |

The 1R trailing stop ends every trend trade long before its 5R target. Too few trades to conclude,
but the trail was designed for short-term trades and probably does not suit this strategy.
**Recommended next step:** make the trail a per-strategy param (off for `trend_4h_short`) and
backtest both ways.

## 6. Dashboard

- New **Overview** at `/`: running status, per-broker equity, today, drawdown from peak, needs
  attention, open positions with why they opened, strategies on. The builder moved to `/desk`.
- **Stop all / Resume** in the header on every page; a persistent PAPER / DEMO / LIVE badge.
- **Strategies page**: every strategy, on/off, symbols, confirmed "trade on Delta too", measured
  record in R, settings form from its schema.
- Settings: 7 tabs, rarely needed fields under "Advanced"; turning on Delta automation is confirmed.
- Confirmations added on: the one-click option order (two-step, real-money variant, broker
  readiness), the perp order, Run a cycle now, trading jobs on the System page, Resume on System.
  One request id per ticket, so a double click sends one order.
- Fixed: trade detail route, journal orders for any day, journal "today" in the trading timezone,
  login open redirect, positions bar no longer adds paper and Delta, Market page polls only enabled
  underlyings, exits editor bounds, mobile save bar above the tab bar.

## 7. Behaviour changes to know before restarting

1. **Your two open `trend_4h_short` paper positions now take the BTC and ETH slots**: no option
   structure opens on those underlyings until they close (one automatic position per underlying).
2. Five perp strategies and four option structures you had on are gone; their open positions (none
   today) would still be managed.
3. Stop all now also refuses manual entries until you press Resume.
4. Unchanged: your `risk_per_trade_pct` stays 2% (the new default 0.5% applies only to fresh installs).
   At 2% and the 4% heat cap, at most two automatic positions fit per broker. Consider 0.5–1%.
5. **Your dashboard PIN is still the published default `242425`** (the startup warning now shows
   on the System page). Real money is refused until it is changed.

## 8. Not done, and why

| Item | Why not now |
|---|---|
| Options replay backtest over `data/chains/` | 5 days of recorded chains: nothing to replay yet |
| Empirical return distribution for POP/EV | changes every option decision; needs its own evidence first |
| Event calendar beyond FOMC | needs a data source; `event_times` still takes them by hand |
| One "today" (UTC vs IST) for bias and risk | changes the stored daily bias series; separate change |
| Push live P&L over the WebSocket instead of polling | performance only; polling is correct |
| Generated frontend types from OpenAPI | adds a dev dependency; types are hand-checked by `tsc` today |
| mypy | the codebase has never been typed-checked; enabling it is its own clean-up |
| Unused `export`s in the frontend | harmless; listed in `05_ui_review.md` |
| Backup databases in `backend/data/` | your data: delete `tradebuddy.before-dummy.db` and `tradebuddy.pre-trend4h.db` yourself if unneeded |
