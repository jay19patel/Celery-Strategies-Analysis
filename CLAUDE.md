# Engineering Guide

## System Purpose

TradeBuddy turns strategy signals into orders on pluggable brokers: the
built-in paper broker and Delta Exchange India, independently, either or both.

It runs as containers under `docker compose up`: feed · engine · web ·
analyst · Celery workers, joined by ZeroMQ, with Redis as the Celery broker.

**Paper is active and trading by default. Delta is opt-in, defaults to demo,
and its trading switch is off at every start.** Runtime settings live in
SQLite and are edited on the Settings page; `.env` only holds process
bootstrap.

## Architecture

Event-driven. Inside a process, components talk through `EventBus`
(`events.py`): each subscriber has its own queue and worker, so it handles
events in order and never blocks another. Between processes, events cross
ZeroMQ as JSON (`codec.py`, `transport.py`).

```
feed    DeltaStream + BarCloser ── PUB ──► engine
engine  PriceBook · StrategyRunner · Trader · Executor · brokers · SQLite
          CandleClosed ─► StrategyRunner ─► evaluator.submit(job)
              inline: evaluate() on the loop      celery: send_task ─► worker ─► PUSH
          StrategyEvaluated ─► StrategyRunner ─► SignalGenerated ─► Trader
          Trader ─► OrderRequested (one per active broker) ─► Executor ─► broker
          dashboard order ─► Trader.manual (same gate) ─► OrderRequested ─► Executor
        ── PUB every event ──► web (dashboard /ws), feed (SettingsChanged)
        ◄─ RPC (api.METHODS) ── web
worker  Celery task: evaluate() one strategy on one bar, PUSH the result
feed    option tickers (WebSocket, REST fallback) ─► OptionsBook ─► OptionsSnapshot every 15s
analyst every 5 min: candles+OI+funding ─► insights ─► Forecaster ─► playbook ─► Mistral? ─► PUSH MarketAnalysis
```

- `System` (`system.py`) is the engine. It is the only writer of orders,
  settings and toggles, so the engine needs no locks. Never make a worker or
  the web process write trading state.
- `runner.evaluate()` is the one evaluation path, used inline and on workers.
- `api.Api` is everything the dashboard can do; `RemoteApi` is the same over
  RPC. Add a method to `METHODS` or the engine refuses it.
- `brokers/base.py` — the `Broker` protocol. `PaperBroker` and `DeltaBroker` implement it.
- `delta.py` — the only code that talks to Delta REST.
- `stream.py` — the only code that talks to the Delta WebSocket.
- `trading.py` — the only code that decides to trade (`Trader`, signals and manual
  orders alike) and sends orders (`Executor`). Dashboard actions name one broker;
  nothing acts on "all brokers" except the kill switch. It also decides how far the
  stop sits and how big the position is: `Trader.levels` (the strategy's own levels,
  else `atr_stop_mult` x the ATR the analyst measured, else Settings' percentages),
  `Trader.size_for_risk` (the position whose stop costs `risk_per_trade_pct` of equity,
  capped by `trade_margin_pct`), `Trader.edge_error` (a target inside
  `min_edge_multiple` round trips is refused) and `Trader.strategy_halt` (a strategy whose
  closed trades have lost `strategy_loss_limit_pct` of the day's starting equity on a broker
  opens nothing more there until tomorrow, while the others carry on).
- `guard.py` — `PositionGuard`, an engine loop over every active broker: the broker-wide daily loss
  limit (closes that broker's positions, blocks its entries until the next day) and
  auto trailing (per-position switch and max trails in `position_controls`). It moves
  SL/TP only through `Broker.update_protection`, under `System.protection_lock`.
- `settings.py` — validation for every runtime setting; `System._apply` applies them.
- `strategies/` — signal logic only. `tb_master.py` is the all-in-one: 1d bias, 1h breakout (swing) or 15m
  squeeze (scalp), confirmed by rising open interest, options walls cap the target; its `decide()` is pure so
  the backtest runs the code that trades.
- `options.py` — the options book and its arithmetic (ATM IV, skew, put/call, max pain, walls).
- `structures.py` — defined-risk option structures (straddle, strangle, iron condor, debit spreads) built from
  the live chain, and the suggestion (playbook, else IV rank + OI). Placed through `Trader.manual_structure`, on
  paper only (`STRUCTURE_BROKERS`): by a person from the ticket, or by `trading.AutoStructures` when
  `options_auto_enabled` (rules on the engine's own chain only, never the playbook or AI; one per underlying,
  30-min cooldown, 3 a day, sized so the max loss is `options_auto_risk_pct` of equity).
  `brokers/paper_options.py` holds them, reserves the max loss as margin and closes on
  whole-structure SL/TP or an hour before expiry — on `realisable()`, what closing would
  actually book, never the mark.
- `email_report.py` — `DailyReporter`, an engine job: the day's report by email once at `email_report_hour`.
- `insights.py` (rules), `forecast.py` (scikit-learn, `ml` extra), `playbook.py` (options
  structure), `tbai.py` + `mistral.py` (TradeBuddy AI report; every attempt saved in full in `ai_reports`,
  90 days, failed 14), `analyst.py` (runs them). Analysis is advisory:
  nothing in it places an order.
  A forecast target must ask the same question on every symbol and in every regime, or one model
  learns a rare event and another a common one and neither is comparable: a breakout is
  `breakout_sigma` x what the bar's own volatility implies over the horizon, never a fixed
  percentage. Keep the models small — a 24h crypto target is mostly noise, and capacity goes into
  the training regime. Every baseline must be the best thing someone could do without the model,
  not a coin: direction is scored against always naming the side that came up more often, which
  over a rising quarter was 54.8% on BTC and 56.2% on ETH. Measured that way nothing predicts 24h
  direction here — not more features, not a shorter horizon, not pooling the symbols, not agreeing
  with a trend indicator; all were tried and all were worse. Bump `MODEL_VERSION` whenever a target
  or feature changes; `load` then refuses the old files and says to retrain, which is the point.
- Every forecast is kept with the bar it was made on (`forecasts`), and the engine writes back what
  the market did once the horizon passed (`System.score_forecasts`, `insights.outcome`). Market Data
  draws the two against each other and scores them with `insights.score_forecasts` — always beside
  the baseline that needed no model, because a hit rate alone cannot tell skill from drift.
- `jobs.py` — every background loop wraps a pass in `job.tick()`; the System page shows them all.
- The market itself is kept, not just read: `bars` (closed candles with the open interest and
  funding of the same bar, `System.collect_bars` fetching only what the store is missing),
  `market_snapshots` (the analyst's numbers per bar as typed columns, because base rates are
  counted with SQL and JSON cannot be scanned) and `process_health` (CPU, memory and loop lag per
  process, which the monitor had always measured and never stored). A bar already stored is never
  rewritten — a silently revised past is worse for a base rate than a missing one — and
  `Store.bar_gaps` reports the holes rather than smoothing over them.

## Invariants

Enforced by tests. Breaking one should fail the build.

1. **Fail closed.** Delta is inactive and on demo by default. Becoming
   real-money needs `LIVE_CONFIRM_PHRASE`. `trading:delta` resets to off on
   every start and on any change to the Delta account or keys.
2. **One order per broker × strategy × symbol × bar.** `client_order_id` is derived from
   them and is the orders table primary key. Paper is idempotent on it too. A manual
   order's id is derived from the broker and the dashboard's per-ticket `request_id`.
3. **Timeout is not rejection.** `BrokerTimeout` is looked up by
   `client_order_id`, never resent. Unresolved, the symbol stays blocked on
   that broker.
4. **Brokers are independent.** A signal is gated per broker; the broker is
   fixed when the order is reserved; one broker's state never blocks another.
5. **Closed candles only.** Strategies run on `CandleClosed`; a strategy with
   `lookback` must see the bar that closed.
6. **The broker decides.** Before an entry, open positions are read from the
   broker; if that fails, no trade.
7. **Protected entries.** SL/TP go with the entry (a manual order needs an SL; an SL
   edit places the new legs before cancelling the old) (Delta bracket; paper
   checks every tick, plus liquidation and max hold).
7b. **The stop sizes the trade, and an exit books what it reported.** A position is the
   one whose stop costs `risk_per_trade_pct` of equity, so a wider stop buys a smaller
   position, never a bigger loss. An exit trigger reads what closing would actually
   book, net of the spread and every fee, so a "take profit" can never record a loss.
8. **Absence over staleness.** A WebSocket price older than 30s is no price.
9. **Secrets stay in the process.** No endpoint returns an API key or secret;
   `Settings.public()` masks them and `SettingsChanged` carries field names only.
9b. **The dashboard is private by default.** Everything — pages, API, `/ws` — needs a credential
    (the PIN, or the API token) unless it is in `app.PUBLIC_PATHS`, so a route added later is private until someone lists it. Reads
    were once open so a viewer could watch without unlocking; behind a reverse proxy that meant
    `/api/account` gave the balance and `/ws` streamed every `PositionClosed` with its PnL to
    anyone. `/ws` is checked in the handler, before `accept()`: the middleware never sees a
    WebSocket handshake. Off loopback, `require_safe_bind` needs a PIN or a token;
    `dashboard_loopback_only` excuses the bind address and nothing else, because nothing in the
    process can see what was put in front of the published port. `Api.get_auth_status` is the only
    thing that can tell whether the PIN is still the published default — once changed from the
    dashboard it is a hash in the database and the environment goes on saying 242425 forever.
10. Every refusal to trade is a `TradeSkipped` event with its reason, including a
    broker halted by its daily loss limit and a strategy halted by its own.
11. **Late is stale.** A strategy result that finishes more than a bar after
    its bar closed is dropped; Celery tasks expire after one bar.
12. **ZeroMQ is not durable.** Nothing that must survive a restart travels
    only over ZeroMQ; ZeroMQ endpoints stay on loopback or a private network.
13. **No price, no strategy.** A symbol with no fresh live price is not evaluated;
    the pause is one `TradeSkipped` per outage, and every page shows a banner.
14. **Bounded for a year.** Ticks, heartbeats and `StrategyEvaluated` are never
    stored; the event log is pruned by `RETENTION_DAYS`; state changes, not
    retries, become events. The orders and paper tables are the permanent record.
    Every stored series has a retention rule wired into `System.housekeeping` and its cost per day
    written beside it in `store.TABLE_INFO`. Counters (`EventBus`) are in-memory and report the
    last completed hour next to the lifetime total: a number that only grows is not readable.
15. **Analysis never trades and never leaks.** The analyst reads market data and
    the engine's `ai_digest` and writes only events. A forecast model is used only
    for what it beat a naive baseline at on unseen data (`ModelCard.skill`).
    TradeBuddy AI (TB-AI) sends Mistral computed numbers and findings, never keys,
    secrets or order ids; trades, positions and account figures only with
    `ai_share_account`. After a 429 it waits as long as Mistral asks (backoff
    5 → 60 min). Every AI-written text in the UI carries the TB-AI badge.

## Extension Points

### Strategy

One module in `tradebuddy/strategies/` with a concrete `Strategy` subclass; it
is discovered automatically. Keep `name` stable, bump `version` when logic
changes. Strategies return a `Signal` or `None`; they never place orders.
`is_default_sl_tp = True` (default) leaves the levels to the Trader: the ATR when the analyst has
measured one, otherwise Settings' percentages. `False` means every Signal carries both
`stop_loss_pct` and `take_profit_pct` (within Settings' ranges), or the result is an error and
nothing trades — that is how a strategy puts its stop where its own logic says (an EMA, a
Supertrend, the mother candle's range).

A strategy runs on whichever Celery worker is free, so it must keep **no state between bars**.
To compare the options market with where it was, set `options_history_hours` and read
`ctx.market.option_history(underlying, hours)`: the engine reads its own `options_history` and
sends the rows with the job.

### Broker

Implement `Broker` (`brokers/base.py`) — including `size_for_notional` and
`round_trip_cost_pct`, which the Trader sizes and gates with — register it in `System.brokers`, add
it to `BROKERS`, a `<name>_active` field in `settings.py`, and a default in
`TRADING_DEFAULT`. It must be idempotent on
`client_order_id` and raise `BrokerTimeout` (not `BrokerError`) when the
outcome is unknown.

### Setting

Add a field to `Settings`, a range or choice in `settings.py`, and an input on
`templates/settings.html`. If it changes where orders or prices go, add it to
`ROUTING_FIELDS`.

### Page

A template extending `base.html`, an entry in `PAGES` in `app.py`, and JSON
routes that call `System`. State-changing routes take `Depends(protected)`; every route, read
included, is already behind the PIN by the middleware (invariant 9b), so a new read needs nothing
added to stay private. Live updates come from `TB.on(eventType, fn)`.

Every number belongs to one page. `/` is Market Data: the market, its options and how the forecast
has been doing. Balances and the paper record are on Account, open positions on Positions, the event
stream on Event Log. Sidebar groups appear in the order their first page does in `PAGES`. There was
an Overview above all of them that showed a copy of each; it is gone, and `/overview`, `/market` and
`/paper` redirect. Resist adding it back — a second copy of a number is a second thing to keep right.

## Commands

The source is not bind-mounted into the containers. The engine is one long-lived process while Celery
children re-import on every respawn, so a mounted checkout lets them drift onto different strategy
versions and every result is dropped as "version mismatch" — which is how one deploy left the engine
dispatching a withdrawn strategy version for a day. `docker compose up` is the only way code moves,
and it moves for every process at once.

```bash
docker compose up          # everything: http://127.0.0.1:8080 (rebuilds on every up)
make install && make check # local lint + tests
make token                 # an API_TOKEN for .env
docker compose run --rm analyst backfill --days 365   # fill `bars` with past candles, OI and funding
docker compose run --rm analyst train --symbols BTCUSD ETHUSD --days 365   # fit the forecaster (data/models)
cp data/models/forecast_* models/   # ship them: models/ is in git and the image, used when data/models has none
```

Tests run the engine in one process (`app.local_app`, inline evaluator, fake
exchange) so they need neither Docker nor Redis.

## Change Rules

- Keep live trading fail-closed.
- Parameterised SQL only.
- Never return a credential from an endpoint.
- Add a dependency only with a runtime owner.
- Every behaviour change ships with a test. Say so when behaviour changes.
