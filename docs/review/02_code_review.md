# 02 — Code & Architecture Review

Branch `trading_system`, 2026-11-07. Every finding below was read in the code; the ones marked ✔ were
re-checked line by line after a first pass. Severity is about money and safety first, then the design
goal (one-file strategies).

**Overall:** the execution core (`trading/service.py`, `brokers/`, `guard.py`, `edge.py`) is
well-built: idempotent structure and order ids, bought-legs-first ordering, timeout ≠ rejection,
fail-closed live switch, 372 passing tests that enforce the invariants. The weaknesses are at the
edges: crash recovery, reconciliation with the exchange, portfolio-level risk, the strategy coupling,
a sprawl of analysis code, and UI actions that trade without confirmation.

## Critical

| Severity | File:line | Issue | Why it matters | Fix |
|---|---|---|---|---|
| Critical ✔ | `trading/service.py:417,566,729`; `guard.py:63`; `service.py:723` | A crash (or kill) between saving a structure as `opening`/`closing` and finishing leaves it in that status forever. The guard only reads `open`/`error`; `close()` refuses anything else; `_ready()` refuses every new entry on that broker while it exists. | After a restart: the broker is silently blocked, the UI cannot close it, and on Delta any leg that did fill is a real position nobody manages. A perp that filled before `_protect()` ran has **no exchange stop and no guard**. | On engine start, move every `opening`/`closing` structure to `error` with "interrupted by restart" and let the existing resolve-then-close path (`_resolve_unknown`, guard retry) handle it. Test: kill mid-`_enter`, restart, assert flat or `error` with alert. |
| Critical ✔ | `brokers/delta.py:41-53`; `engine.py:108` | Exchange positions and open orders are never compared with the database. `positions()` is read only to compute equity. | A manual trade on the Delta app, a stop that filled while the process was down, a partial fill the DB missed, or the crash above all leave the DB and the exchange disagreeing with no alert. | Reconciler on start and every 5 min: positions and open stop orders vs `HELD` structures; mismatch → error event, Telegram, block entries on that broker. Never auto-correct. |
| Critical | `engine.py:3-5` | "Run as a single worker" is a comment, not a guard. | A second `uvicorn` (or `--reload` mishap) is a second engine. Option decisions use DB autoincrement ids (`auto-<id>`), so two engines open two structures for the same idea. | Take an exclusive `fcntl` lock on a file next to the DB in `Engine.start()`; refuse to start if held. |

## High

| Severity | File:line | Issue | Why it matters | Fix |
|---|---|---|---|---|
| High ✔ | `notify/notifier.py:91`; `main.py:43` | The Telegram bot token is part of the request URL, and httpx logs every request URL at INFO, which `basicConfig(INFO)` prints. | Breaks invariant 9 ("secrets stay in the process"): the token lands in stdout, journald, any log shipper. | `logging.getLogger("httpx").setLevel(logging.WARNING)` in `main.py`; add a test that captures logs during a Telegram send and asserts the token is absent. |
| High ✔ | `trading/service.py:779`; `api/routes/trading.py:61` | "Close all" closes positions but leaves auto-trading on (the UI says so). | It is not a kill switch: the next cycle re-opens. Under stress the user believes they stopped the bot. | Stop all = persist auto off on every broker, then close all, then report what is left. Separate resume. |
| High ✔ | `frontend/src/components/desk/direct-option-modal.tsx:298`, `desk/direct-perp-panel.tsx:374`, `app/(dashboard)/strategies/page.tsx:47`, `system/page.tsx:246`, `auto/page.tsx:105`, `settings/sections.tsx:208` | Eight UI actions can place orders, or start live automation, with no confirmation, no real-money notice and no halt check. | One mis-click places a live order on Delta. The guarded path (`order-panel.tsx`) shows the right pattern. | Reuse `Confirm` with the real-money variant everywhere; see `05_ui_review.md` §6. |
| High ✔ | `options/assess.py:101-104` | The "edge at realised vol" check is added only when an EV exists. Missing realised vol (< 200 stored bars) removes a required check instead of failing it. | Fail-open on the one check that measures the selling edge. A fresh install or a new underlying sells premium on POP alone. | `check("edge at realised vol", False, "no realised volatility yet")` when `ev is None`; test with an empty bar store. |
| High ✔ | `trading/service.py:502,541,570,603` | Perp sizing and margin use `paper_leverage` for every broker, Delta included. | Delta's leverage is set per product on the exchange. Margin math, `available` checks and recorded margin are wrong on Delta; with exchange leverage lower than 10 an order is refused late or margin is overstated. | Read leverage from the Delta position/product (or set it via API before entry); keep `paper_leverage` for paper only. |
| High ✔ | `market/feed.py:170-180`; `core/models.py:136` (Bar keyed by symbol) | Switching `market_data` or activating Delta on demo points the feed at testnet but keeps writing to the same `BTCUSD` rows. | Testnet prints enter the production bar history used by regime, realised vol and backtests. `on_conflict_do_nothing` means the bad rows persist. | Key stored market data by environment (or refuse to store when `data_env == "demo"`); clear `_latest_bar_times` and tape on reconfigure. |
| High ✔ | `market/tape.py:81-85` | `_last` is not updated after a rejected print. | A junk first print or a > 5 % gap across an outage makes the tape reject every later trade until restart. The flow strategies and the bias silently run on frozen data. | Reset `_last` from the perp mark when rejections persist (e.g. 20 in a row), or compare against the mark instead of the last print. |
| High ✔ | `analysis/flow.py:33-62`; `analysis/service.py:157`; `strategies/auction.py:182-185` | `flow.fold` keeps the forming 5m bucket and `MarketDesk.flow` has no age check. `flow_breakout` pairs a closed candle with `delta`/`stacked` from a newer, partial bucket, or from hours-old tape after a socket outage. | Look-ahead-style misalignment in live, and trading on stale data. | Drop buckets newer than the strategy's last closed bar; mark flow not-ready when the newest minute is older than 2 bars. |
| High | `trading/service.py:376-383`; `autotrader.py:99-113`; `zerohero.py:315`; `service.py:524` | Each family counts its own slots. No cap on total open risk or on per-underlying exposure across families. | One broker can hold an option structure, a perp, a zero-hero buy and a decay spread on BTC at once, in opposing directions, each sized at 2 %. | One `RiskManager`: portfolio heat ≤ 4 % equity, one position per underlying across families (`04_target_architecture.md` §3.1). |
| High ✔ | `strategies/__init__.py:14`; `settings.py:29-45`; `options/strategies.py:41,252`; `options/regime.py:146-202`; `frontend/src/components/desk/strategy-picker.tsx:25` | A strategy is wired in 3–6 places; `STRATEGY_KEYS` and the `Strategy` Literal are two copies of one list. | Fails the core design goal; the frontend copy is already wrong (`short_call`/`short_put` cannot set delta). | Plugin registry with auto-discovery (`04_target_architecture.md` §2). |
| High ✔ | `api/routes/auth.py:16-49`; `config.py:20`; `main.py:53` | Login lockout is keyed by client IP, but every browser request arrives through the Next.js proxy: one global bucket. Default PIN `242425` is in the source. The default-PIN warning goes to a logger the event log does not capture. | Anyone reaching the dashboard can lock the owner out; a default PIN on a box that can trade real money. | Refuse to start with `delta_env == "live"` while the PIN is default; log the warning on an `app.*` logger; key lockout on `X-Forwarded-For` from the trusted proxy plus a global cap. |
| High ✔ | `settings.py:75,84` | Every directional strategy and every option structure is on by default, including ones the code itself says lose (`settings.py:72-74`) and unbounded short options. | Paper results are polluted by known losers; one click on `auto_delta` arms them (edge gating then zero-sizes unproven ones, which is the only thing stopping it). | Delete the losers (`03_strategy_review.md`); new strategies default off. |

## Medium

| Severity | File:line | Issue | Why it matters | Fix |
|---|---|---|---|---|
| Medium ✔ | `trading/service.py:409-436, 944-952` | The single `asyncio.Lock` is held across broker calls, including the timeout lookup sleeps (1+3+10 s per leg). | A slow Delta during a 4-leg entry blocks every guard exit for up to ~1 min. | Keep the lock for state transitions only, or give the guard's exits priority (e.g. the guard does not wait behind an entry's lookup). |
| Medium ✔ | `trading/service.py:631-676` | `trail()` decides on a `Structure` loaded before taking the lock and does not re-read it inside. | A close between load and lock can lead to placing a stop for a closed position (Delta rejects reduce-only, but an error event fires). | Re-load the structure under the lock and return if not `open`. |
| Medium ✔ | `api/routes/trading.py:56` | `update_exits` is a sync route (threadpool) writing `Structure` while the guard writes it on the loop. | Lost update; contradicts "single writer". | Make it `async` and run under `trading.lock`. |
| Medium | `market/feed.py:197`, sync routes in `api/routes/market.py`, `zerohero.py` | Threadpool routes iterate `feed.book` while the WS handler mutates it. | "dictionary changed size during iteration" → 500s. | Make those routes `async` (they do no blocking I/O) or copy under the loop. |
| Medium ✔ | `api/routes/desk.py:41` → `analysis/service.py:297` | `limit` is unclamped; trendline detection is O(swings² × bars) on the event loop. | An authenticated GET can stall the 5 s guard. | `limit: int = Query(500, le=1500)` and `asyncio.to_thread` for the computation. |
| Medium | `core/events.py:65`, `analysis/service.py`, `market/feed.py` (collect/store), `main.py:67` | Synchronous SQLite inside async code across the hub, feed, analysis and the auth middleware. | Each call is short in WAL, but they add up next to a 5 s guard; a long `maintain()` or a locked DB stalls everything. | Move bulk writes (`_store`, `record_*`) to `to_thread`; keep single-row writes. |
| Medium ✔ | `core/scheduler.py:123-127` | Manual "Run" calls the job function directly, bypassing `max_instances=1`. | Overlapping auto cycles; duplicate decision rows. | Run manual triggers through `scheduler.modify(next_run_time=now)`. |
| Medium | `market/feed.py:395, 442` | Any parse exception in `handle()` drops the socket; a socket outage only logs a warning, no hub event. | Silent data gaps; the user is not told the feed is down. | Catch per message; publish `FeedDown`/`FeedUp` events and show them on the overview. |
| Medium | `market/delta.py:113-124` | No client-side rate limit; 429 is a generic error with no backoff; 5xx on GET is not retried. | Bursty polling (accounts every 3 s from the UI) plus order traffic can trip Delta's limits at the worst time. | Small token bucket in `DeltaClient.request`; back off on 429; cache `account()` for a few seconds. |
| Medium | `market/delta.py:72,150` | `_products` caches every option symbol forever. | Violates invariant 13 (no cache keyed by option listings) on a years-long process. | Key by underlying for options, as `Desk._fee_key` already does, or TTL. |
| Medium | `trading/runner.py:163-167` | Funding is charged once as a cost, whatever the hold (`trend_4h` holds up to 10 days). | Under-costs long holds by up to ~30 funding payments. | Expected funding = rate × expected hold / 8h. |
| Medium | `options/zerohero.py:257` vs `trading/service.py:108` | Zero-hero EV assumes exit at the target multiple (≥ 6x); the guard takes profit at 300 % (4x). | The gate's EV does not describe the executed trade. | Moot if zero-hero is removed; otherwise compute EV at the TP multiple. |
| Medium | `analysis/service.py:177,210`; `analysis/bias.py:41` vs `trading/risk.py:13` | "Today" is UTC midnight for bias/flow/profile and `day_timezone` (IST) for risk and stop-out rules. | A gate and the risk day disagree for 5.5 h every day. | One `trading_day()` used everywhere. |
| Medium | `backtest/perp.py:51-95` | Backtest omits the live trailing stop, the bias gate, the stopped-today rule and stop slippage. | Backtest and live are different strategies; results cannot be compared. | Share the exit logic between guard and backtest. |
| Medium | `trading/risk.py:31-38`, `guard.py:200` | The day's baseline equity is whatever the first check of the day reads. | If the engine is down at the day boundary, losses before it starts are not counted. | Record start-of-day equity from the stored equity series, or at the boundary job. |

## Low

| Severity | File:line | Issue | Why it matters | Fix |
|---|---|---|---|---|
| Low | throughout (`core/models.py`) | Money and prices are `float`; `Decimal` only at the order boundary (`market/delta.py:59`). | At these sizes float error is ~1e-12 of a dollar; rounding to 6 dp on save is adequate. Decimal everywhere would be churn without benefit. | Keep float; keep Decimal at the exchange boundary; round sizes to lot (already int). |
| Low | `market/delta.py:59-61` | Tick rounding is to nearest, not toward marketable. | With slack > half a tick (always true here) it is harmless. | Round buys up, sells down. |
| Low | `api/routes/portfolio.py:65,104` | Naive `datetime.now()` (server local time). | Off-by-one day in equity cutoff and journal default month when server TZ ≠ `day_timezone`. | Use `ZoneInfo(day_timezone)`. |
| Low | `main.py:38` | `/docs`, `/openapi.json` are public (outside `/api`). | Exposes the full API surface. | Disable in production or put behind the session. |
| Low | `core/security.py:71-84`; `api/routes/auth.py:50` | Sessions not revocable; PIN change does not rotate the secret; cookie lacks `secure`. | A stolen cookie lives 7 days. | Rotate `auth:secret` on PIN change; `secure=True` when served over HTTPS. |
| Low | `core/db.py`; `settings.py` | Delta API secret, Telegram token, SMTP password stored in plain text in SQLite. | Anyone with the DB file has the keys. Acceptable on a single-user machine if the file is `0600`. | `chmod 600` on create; document; optionally keep exchange keys in `.env` like the LLM keys. |
| Low | `core/events.py:48` | Subscriber queues unbounded. | A stuck SMTP server grows memory without limit. | Bound to 1,000, drop oldest, count drops. |
| Low | `trading/service.py:1016`; `api/routes/portfolio.py:151` | `stats()` loads every closed structure; `journal_day` loads the latest 1,000 and filters. | Slow after years; older journal days miss structures. | Query by date range. |
| Low | `trading/service.py:137` | Sharpe/Sortino computed over days with a closed trade only. | Inflates the ratios. | Include zero-P&L days. |
| Low | `market/indicators.py:34,186` | RSI of a flat series is 100; `indicators.fold` drops the last bucket even when complete. | Zero-hero reads lag one 5m bar. | 50 for flat; use `resample.fold`. |
| Low | `analysis/pulse.py:52`; `analysis/outlook.py:360,461` | Hardcoded 15m bar length applied to 5m and 1h/1d bars. | Wrong magnitudes and horizon labels (display; pulse state unaffected). | Pass the bar length. |
| Low | `analysis/*` | Three realised-vol functions, four bar folders, two value-area builders, two EMAs. | Divergent semantics (see `01_system_map.md` §4). | Keep one of each. |

## Dead code and stale text (delete per the "remove dead code" rule)
- `Decision.ai` column; frontend AI-review block in `components/auto/decision-row.tsx:34,89-107`; README §"How the auto-trader decides" step 4 (AI approval) contradicts invariant 11.
- `indicators.macd`, `reading._series`, `reading.TIMEFRAMES`, `liquidity.untouched`.
- Display-only work computed every bar inside `MarketDesk.structure()` (trendlines, smc, candle orderflow) — move behind the page that shows them.
- Stale docstrings: `analysis/structure.py:4` (Celery), `analysis/outlook.py:344` (docker compose), `analysis/pulse.py:35` (`verdict.py`), `analysis/orderflow.py:3` ("no tape yet").
- Backup databases in `backend/data/` (`tradebuddy.before-dummy.db`, `tradebuddy.pre-trend4h.db`).

## Observability — can a trade be reconstructed?
Mostly **yes**. `Decision` rows store regime, every candidate, why each was dropped and each broker's
result; `SignalLog` stores perp opens with reason, stop, target; `Structure.reason`, `OrderRecord`
per leg with limit, fill, fee and exchange id; `EventLog` for warnings/errors. Gaps:
- Logs are plain text, not structured; no `structure_id`/`decision_id` on log lines.
- Perp signals that did not trade are not stored (by design), so "why didn't it trade yesterday?" has no answer after a restart.
- No version of the strategy code is recorded on the trade; a changed `decide()` is invisible in history. Add `strategy_version` to `Structure`.

## Testing — what is and is not covered
**Covered well** (372 tests): leg ordering and unwind, timeout-lookup, idempotent ids, guard exits and
stop confirmation, hold-to-settlement, daily halt, edge scaling, settings fail-closed rules, secrets
masking, route privacy, every strategy's `decide()`, perp backtest mechanics, analysis modules.

**Not covered:**
- Crash/restart recovery of `opening`/`closing` structures (the Critical above).
- Reconciliation (does not exist).
- `DeltaBroker` against recorded real API responses beyond stop placement (2 tests); partial fills on Delta.
- WebSocket reconnect, tape rejection lockout, stale flow.
- Concurrency: threadpool routes vs the loop, manual job overlap.
- Logs containing secrets.
- Frontend: 8 tests (formatting and derive helpers); no component or interaction tests for any trade-placing UI.

## Performance
- ~46 SWR polling intervals; `/positions` polls accounts and structures every 3 s, and accounts hits Delta REST when active.
- Per-bar recomputation of display-only analysis.
- Unclamped trendlines (above).
- Otherwise fine for the scale: SQLite queries are indexed on the hot paths; no N+1 in the trading service (`structures()` fetches legs in one query).
