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
analyst every 5 min: candles+OI+funding ─► insights ─► PUSH MarketAnalysis
```

- `System` (`system.py`) is the engine. It is the only writer of orders,
  settings and toggles, so the engine needs no locks. Never make a worker or
  the web process write trading state.
- `runner.evaluate()` is the one evaluation path, used inline and on workers. The bars come WITH
  the job: the engine reads the series once per symbol per close from the `bars` table it fills
  itself and hands each strategy its slice. They used to be fetched inside the evaluation, once
  per strategy per symbol — five strategies on the 15-minute close meant ten REST calls for one
  series already on disk — and a worker now makes no market request at all. An empty read falls
  back to REST rather than handing every strategy zero bars.
- `api.Api` is everything the dashboard can do; `RemoteApi` is the same over
  RPC. Add a method to `METHODS` or the engine refuses it.
- `brokers/base.py` — the `Broker` protocol. `PaperBroker` and `DeltaBroker` implement it.
- `delta.py` — the only code that talks to Delta REST.
- `stream.py` — the only code that talks to the Delta WebSocket.
- `trading.py` — the only code that decides to trade (`Trader`, signals and manual
  orders alike) and sends orders (`Executor`). There is one position per symbol, so every strategy
  is competing for one slot; `Trader._settle` holds a symbol's signals for `signal_settle_seconds`
  and sends the best **reward-to-risk after costs**, refusing the rest by name. It used to be
  first-come, which is a race and a biased one — a strategy on a 5m bar gets three times the
  chances of one on a 15m bar, so the FASTEST strategy took the slot whatever its trade looked
  like, and on the deployed system one strategy that fired on every bar took 78% of all signals
  that way. That race is why the 5m strategies shipped switched off; fixing it is what let them be
  switched on, and **every strategy now arrives `default_on = True`**. 0 restores first-come. Dashboard actions name one broker;
  nothing acts on "all brokers" except the kill switch. It also decides how far the
  stop sits and how big the position is: `Trader.levels` (the strategy's own levels,
  else `atr_stop_mult` x the ATR the analyst measured, else Settings' percentages),
  `Trader.size_for_risk` (the position whose stop costs `risk_per_trade_pct` of equity,
  capped by `trade_margin_pct`), `Trader.edge_error` (a target inside
  `min_edge_multiple` round trips is refused) and `Trader.strategy_halt` (a strategy whose
  closed trades have lost `strategy_loss_limit_pct` of the day's starting equity on a broker
  opens nothing more there until tomorrow, while the others carry on).
- `guard.py` — `PositionGuard`, an engine loop over every active broker: the broker-wide daily loss
  limit (closes that broker's positions, blocks its entries until the next day),
  auto trailing (per-position switch and max trails in `position_controls`), and the early
  exits. It moves SL/TP only through `Broker.update_protection`, under `System.protection_lock`.
  The exit is asked BEFORE the trail: a stop moved on a position about to close is a protection
  change nobody acts on, logged immediately before the exit that overrode it.
- `exits.py` — whether an open position is still worth holding, and the one module here whose
  main finding is that it cannot do what people expect. Price a live bracketed position and
  `EV(hold)` equals `EV(close now)` to the cent — the martingale property — and counting the
  stored bars says the walk really is driftless: over 35,488 15m bars price was higher an hour
  later 50.1% of the time, four hours later 50.2%, a day later 50.4%. So the probability
  arithmetic can never on its own say "close"; it is fair by construction. What it CAN do is
  price the decision honestly, which is worth having alone: a stop 0.42% away against a target
  1.21% away looks like 1:2.9 and is a **74.3% chance of being stopped first**
  (`outlook.p_stop_first` is target/(stop+target) — volatility cancels). An exit therefore has to
  earn its reason where the coin cannot reach, and there are four such places, strongest first:
  **INVALIDATED** (the permission the entry needed is gone — structural, no sample size),
  **STALLED** (most of the risk spent and none of the ground made, measured as a share of the
  position's OWN stop and target so it is scale-free), **TIMEOUT** (`p_neither`, which only bites
  once the horizon is short enough to bind — at a 72h max hold a 15m position times out 0.0% of
  the time, at 4h 11.8%) and **CARRY** (funding, already collected on every stored bar; a short
  being paid to hold is never closed for it). Pure, like `structure.py` and `outlook.py`, so the
  guard, a backtest and the dashboard get one answer. Off until `early_exit_enabled`, but the
  odds are read and shown on every pass whether or not anything is armed to act.
- `settings.py` — validation for every runtime setting; `System._apply` applies them.
- `strategies/` — signal logic only, and only structure-based: **`scalper`, `tb_master`,
  `trade_buddy_manager`**. The indicator strategies (two EMA crossovers, two mother-candle
  variants) and `pcr_options` were removed — they were transforms of price saying the same thing
  as each other, and `pcr_options` had lost more than every other strategy combined. Option
  structures (straddle, strangle, iron condor and the auto-trader that opened them) went with
  them: this is a directional system now. `tb_master.py` is the all-in-one: 1d bias, 1h breakout (swing) or 15m
  squeeze (scalp), confirmed by rising open interest, options walls cap the target; its `decide()` is pure so
  the backtest runs the code that trades.
- `pulse.py` — "is anything happening right now", and the one module whose design is mostly about
  what it refuses to say. Three questions: what the bar that just closed DID (volume against the
  96-bar normal, range in ATRs, body share, volatility ratio, squeeze, a close through a prior
  swing) is measured; how FAR price could travel is `outlook.p_touch` and `expected_abs_move`,
  real arithmetic because volatility is persistent; and WHICH WAY is COUNTED — of the bars that
  looked like this one, how many resolved up, against the share of ALL bars that did, with both
  samples. Under `outlook.MIN_SAMPLE` no percentage at all, and none either when the bar is not
  unusual (`worth_counting`): "at least as busy and as wide as this one" matches most of the
  history on an ordinary bar and hands back the baseline wearing a conditional's clothes. The
  asymmetry is the feature — a big move is detectable, its size estimable, its direction usually
  not, and the card renders those three with deliberately unequal confidence. Cached per bar like
  `outlook`; it reads the full stored series because on a genuinely loud bar a few thousand bars
  give 65 matches where a year gives 1,200.
- `options.py` — the options book and its arithmetic (ATM IV, skew, put/call, max pain, walls).
- `email_report.py` — `DailyReporter`, an engine job: the day's report by email once at `email_report_hour`.
- `insights.py` (rules over measured numbers) and `analyst.py` (runs them every 5 minutes, one
  `MarketAnalysis` per symbol). Analysis is advisory: nothing in it places an order. Every *number*
  on a page is arithmetic over its inputs or a rate counted over the stored bars — there is no
  trained model anywhere in this system and nothing is fitted.
- `tbai.py` + `mistral.py` — the written summary, and the only thing here that talks to a language
  model or leaves the machine. It reads the numbers the rest of the system already measured and
  returns prose about them: it computes nothing, decides nothing, and is told not to predict, not
  to recommend a trade and not to use a figure it was not given. `tbai.ALLOWED` is the gate on what
  is sent and it is enforced in `digest()`, so a field added to `market_structure` next year cannot
  start leaving by itself. Off unless switched on; badged `WRITTEN`, the fourth and weakest
  provenance, because it is the one thing on the page that cannot be rechecked against a bar.
- `reading.py` — the measured blocks the page and the engine still read: volume, VWAP, the volume
  profile, candle anatomy, candle-shape records, `regime_at` and `barrier_outcome`. The indicator
  blocks (RSI/MACD/ADX, per-timeframe and their agreement) and the levels block were removed with
  the cards that rendered them: nothing else read them, so they were arithmetic nobody saw.
- `provenance.py` — where every figure came from (arithmetic, counted history, what traders paid),
  ordered by how little has to be true for it to hold. The page badges each one: showing them
  identically is how two claims of very different worth ended up looking the same.
- `demo.py` — `python -m tradebuddy demo` writes a market that never happened into its own database
  (`data/demo.db`), so the pages can be read before real history exists. It refuses a database that
  has ever held an order, and sets `demo_data`, which puts a banner on every page.
- `strategies/trade_buddy_manager.py` — trades the break the Market page shows: a fresh BOS on a bar
  bigger and busier than usual, no higher timeframe against it, and a counted record saying this kind
  of break pays. It reads `ctx.outlook` (the engine's own view, `wants_outlook`) rather than working
  one out, so the strategy and the page can never disagree. Fail closed: without counted history it
  refuses and says to run `backfill`.
- `structure.py` (swings, BOS, CHoCH, FVG, squeeze, resampling), `outlook.py` (the directional view:
  trend on 15m/1h/1d, how far price travels by when, and direction counted over the stored bars) and
  `verdict.py` (one short checkable claim per Market-page card). All three are pure — bars in, numbers
  out, no clock and no network — so the dashboard, a worker and a backtest get the same answer.
  The arithmetic is checked against simulation, not against itself (`tests/test_outlook.py`).
- `liquidity.py`, `zones.py`, `profile.py`, `orderflow.py` — where price reacts, and whether it has
  paid to think so. `liquidity`: fractal pivots (confirmed only `n` bars later, so a pivot is never
  known on the bar it happened), equal-high/low pools with their touch count, **sweeps** (through a
  pool and closed back inside — a raid, not a break), previous day/week/month levels and the UTC
  sessions (Asia 00-08, London 07-16, New York 13-22, overlapping on purpose). `zones`: order blocks
  (the last opposite candle before a displacement that also broke structure — both required), fair
  value gaps and clustered S/R bands, each with a lifecycle that **only moves forward**
  (`fresh → tested → mitigated → invalidated`). `profile`: volume by price — POC, value area, HVN/LVN,
  naked POCs, and a TPO market profile with its initial balance, single prints and poor highs.
  `orderflow`: delta and CVD **estimated from candles and badged APPROX everywhere**, because the
  feed records no trade tape; it earns its place by being the one axis uncorrelated with the price
  transforms (ρ 0.03-0.32 against RSI/MACD/bands, which sit at 0.80-0.97 with each other).
  Every rate any of them reports is shown beside the baseline it has to beat and the sample behind
  it — measured over 35,000 stored bars the sweep edge is +0.2 points, while over 2,000 it reads
  +3.3, which is what a few hundred samples of a coin look like.
  `System.market_structure` builds all four together and caches per bar, like `outlook`.
- `resample.py` — one base series, every higher timeframe folded from it. Two timeframes are stored:
  the base (1m, `System.collect_base_bars`, 90 days) and the one the strategies run on (15m, kept for
  good). Everything else — 5m, 1h, 4h, 1d — is folded from whichever of those divides it, so the
  hourly candle is exactly the four quarter-hours underneath it rather than a second download that
  may disagree. Buckets start at `time // span * span`: epoch seconds are UTC and every interval
  divides the epoch, so the day opens at 00:00 UTC with no timezone arithmetic anywhere. A bar is
  stamped when it OPENED and is emitted only once the source has run past its end — not once it has
  enough bars, which is what stops a half-finished day being drawn as a closed one. Each folded bar
  says how many source bars it was built from, and a bucket under `MIN_COVERAGE` is dropped rather
  than averaged over a hole. The base exists because a fifteen-minute bar cannot see inside itself:
  session levels, an honest volume profile and a liquidity sweep are questions only a minute bar
  answers. Since the 15m series is both stored and foldable, `tests/test_base_bars.py` asserts the
  two agree — that is the condition under which the duplication is allowed.
- `jobs.py` — every background loop wraps a pass in `job.tick()`; the System page shows them all.
- The market itself is kept, not just read: `bars` (closed candles with the open interest and
  funding of the same bar, `System.collect_bars` for 15m and `System.collect_base_bars` for the 1m
  base, each fetching only what the store is missing),
  `market_snapshots` (the analyst's numbers per bar as typed columns, because base rates are
  counted with SQL and JSON cannot be scanned). A bar already stored is never
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
   `lookback` must see the bar that closed. A folded timeframe is emitted only once the source has
   run past its end, so a bar still forming is never served as a closed one.
5c. **A level's state only moves forward.** A zone goes fresh → tested → mitigated → invalidated
   and never back, so running the scan over more history can only advance it. A chart that redraws
   its zones on every refresh cannot be counted, which is the whole point of storing the state.
5d. **A pattern is nothing without its baseline.** Every sweep, zone and divergence rate is
   reported next to what any bar at all would have given over the same horizon, and under its
   minimum sample no rate is shown. Measuring "did price ever trade through this zone" over a year
   answers yes for nearly every zone ever drawn; the question that counts is what happened the
   **first** time price came back.
5b. **One base, every frame derived.** 1m is the only raw series stored below 15m; 5m and 3m are
   folded from it and 1h/4h/1d from the 15m series. Nothing fetches a timeframe separately, and a
   folded bar never changes once later bars arrive (`tests/test_resample.py`) — a value that moves
   when the future arrives is a value that saw it.
6. **The broker decides.** Before an entry, open positions are read from the
   broker; if that fails, no trade.
7. **Protected entries.** SL/TP go with the entry (a manual order needs an SL; an SL
   edit places the new legs before cancelling the old) (Delta bracket; paper
   checks every tick, plus liquidation and max hold).
7b. **The stop sizes the trade, and an exit books what it reported.** A position is the
   one whose stop costs `risk_per_trade_pct` of equity, so a wider stop buys a smaller
   position, never a bigger loss. An exit trigger reads what closing would actually
   book, net of the spread and every fee, so a "take profit" can never record a loss.
7c. **An exit that overrides a stop owes its reason and its odds.** Every early close is a
   `PositionExitEarly` naming which of `exits.py`'s four rules fired and keeping the pricing it
   fired on, so the decision can be rechecked against the bars rather than taken on trust. A rule
   that cannot see what it needs abstains: without the excursions (Delta does not record them)
   STALLED says so instead of guessing, and without a measured volatility nothing is priced at all.
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
    broker halted by its daily loss limit and a strategy halted by its own. Its mirror is
    invariant 7c: every early exit carries its reason too.
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
14b. **Every claim is written down and marked.** A card that says something stores it
    (`predictions`: stance, horizon, what to measure, the numbers behind it) and the engine settles it
    once that horizon passes, beside the baseline that needed no chart at all. A probability is never
    shown without its sample size: under `outlook.MIN_SAMPLE` the page says how many it has and no
    percentage. Direction starts at a coin and is only moved by what was counted. A verdict badge
    reads the same series as the chart it sits on.
15. **Analysis never trades.** The analyst reads market data and writes only events. Every FIGURE
    on a page is arithmetic over its inputs or a rate counted over the stored bars — nothing is
    fitted and nothing is guessed — and `provenance.py` names which.
15b. **One feature sends data off the machine, and it is the only one.** The written summary
    (`tbai.py` + `mistral.py`) posts measured market numbers to Mistral and gets sentences back.
    It is gated three times: `MISTRAL_API_KEY` in the environment, `ai_enabled` in Settings, and a
    per-run check that the numbers exist. What may be sent is `tbai.ALLOWED`, an allowlist
    **enforced in `digest()`**, not a blocklist of words — "position" is ambiguous in a market
    (`range_position_24h` is a price statistic), so a word filter would either miss real leaks or
    block real data and a reviewer could not tell which. **No balance, position, order, P&L or
    credential is ever in a digest**, and the key lives in the environment so nothing can read it
    back out of the database. The model computes nothing, decides nothing and never places an
    order; its answer is capped and shape-checked by `tbai.clean` before it is stored.
15c. **Prose is the weakest provenance and is badged as such.** `provenance.WRITTEN` sits below
    arithmetic, counted history and what traders paid, because it is the only thing on a page that
    cannot be rechecked against the bars it came from. A sentence a model wrote must never be
    rendered with the same weight as a number the system counted.

## Extension Points

### Strategy

One module in `tradebuddy/strategies/` with a concrete `Strategy` subclass; it
is discovered automatically. `wants_outlook` and `wants_structure` hand it the engine's own
directional view and market-structure read through `ctx` — the same readings the dashboard draws,
so a strategy and the page can never disagree about where price reacts. Both arrive WITH the job,
like the bars: a worker has no database. Keep `name` stable, bump `version` when logic
changes. Strategies return a `Signal` or `None`; they never place orders.
`ctx.round_trip` is what one round trip costs on the broker the signal would reach, sent with the
job like the bars and the outlook. Gate on `base.net_rr` (reward and risk BOTH moved by the cost),
never on `target/stop`: at a 0.14% round trip a 0.12% stop against a 0.12% target reads 1.0 gross
and is negative net. Floors come from `base.min_stop_pct`, so a stop minimum tracks the fee instead
of being a number that was right when it was typed. There used to be three definitions of this one
figure — a constant in `tb_master`, `Broker.round_trip_cost_pct` for `Trader.edge_error`, and the
Settings fee — and changing the fee moved two of them.

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

The Market page opens with **Right now** (`pulse.py`): one state word, the numbers that earned it,
how far price could travel, and what followed bars like this one. It sits above everything because
it is the question asked before any of the others, and the only thing that answered it before was
one line inside a findings list sorted by severity. Its three panels are deliberately unequal —
measured, arithmetic, counted — and the counted one shows a bar against its baseline or nothing.

Every number belongs to one page. `/` is Market Data: the market, its options and what the rules
read in them. Balances and the paper record are on Account, open positions on Positions, the event
stream on Event Log. Sidebar groups appear in the order their first page does in `PAGES`. There was
an Overview above all of them that showed a copy of each; it is gone, and `/overview`, `/market` and
`/paper` redirect. Resist adding it back — a second copy of a number is a second thing to keep right.

**The tape is the page**, and it is the one thing here drawn by a library: TradingView's
Lightweight Charts (`static/js/vendor/`, Apache 2.0, **vendored not CDN** — a trading dashboard
should not lose its chart when unpkg has a bad day). Everything else stays hand-rolled SVG in
`chart.js`; a bar chart does not need 160 KB to draw six bars. A price chart does — zoom, pan, a
crosshair that snaps to bars, a price scale that stays readable at every zoom, and a canvas that
redraws ten thousand candles smoothly are each a week of work, and the hand-rolled version that
stood here was already the most delicate code on the page.

`static/js/tape.js` owns it. The library draws candles, volume, price lines and markers; it has no
notion of a shaded zone or a volume-by-price gutter, so those go on our own canvas **underneath**
it — chart background transparent, our canvas behind — both driven by the library's own
`priceToCoordinate` and `timeToCoordinate`, so a zone cannot drift from the candle it belongs to.
Only some levels get a label: eleven labelled lines inside a few hundred dollars stack into an
unreadable block, so the nearest each way and anything with confluence are named and the rest are
drawn plain. Sweep markers carry no text for the same reason — twenty of them is a purple cloud
with the chart behind it. The chart is mounted **once** and updated, never rebuilt: a poll that
remounts it throws away the zoom, which is the one thing on the page a person sets by hand.

The page shows a verdict badge per card, and `verdict.py` builds one per card that EXISTS — no
more. Three (indicators, levels, candle shapes) outlived their cards and went on being computed,
stored and settled every bar for nobody; `tests/test_reading.py` now asserts the stronger form,
that every verdict has a badge on the page to appear in.

`tools/render_check.py` stubs the library and records what the page *handed* it, so a renamed field
that leaves the chart drawing candles and nothing else fails the build (`tests/test_web.py`) — a
chart with no zones on it looks exactly like a market with no zones in it. `tools/snapshot.py`
is the other half: it runs the real page, real scripts and real library in headless Chrome against
captured payloads and photographs the result, because a stub DOM cannot run a canvas library and a
screenshot of one shows the stub.

**The tape has its own page** (`/tape`, `templates/tape.html`). It is the one thing here read by
zooming, panning and hovering, and all three want room: as a card among a dozen it was 440px of a
scrolling page. The chart fills the window and the controls sit in a rail down the side, because a
button row above a chart costs the chart that height whether or not anyone is touching it. There
is exactly **one** of these — Market Data links to it rather than drawing a second copy, since two
charts of one market are two things to keep right and they drift. `TBTape.mount` is shared; the
page works nothing out for itself. The hover reading goes to a **fixed panel in the rail**
(`mount({panel})`), not a card that follows the cursor: the reading is most of why anyone hovers,
and a floating card sits on top of the candle being pointed at — the one place the eye already is.
It falls back to the newest bar when the pointer leaves, because a panel that empties flickers on
every reach for the scrollbar and an empty box reads as "nothing here". Levels further than `LEVEL_REACH_ATR` are not drawn: a price
line makes the library widen its own scale to include it, so one level five ATRs away silently
costs the candles a third of their height — and `nearby` lists every one of them anyway.

Market Data is **one view, not tabs.** It was eight — patterns, volume, indicators, open interest,
liquidity, profile, flow, findings — and seven of them were lists of PRICES. A price belongs on a
price axis, so they are all drawn on one chart (now on `/tape`) against one scale: zones as shaded
rectangles from where they formed, every named level as a line, sweeps as hooks, the volume profile
in the right-hand gutter, delta or volume underneath. The layer buttons choose what is visible and
the choice is remembered; the timeframe buttons refold the same stored base (§`resample.py`).
There is no separate structure card and no candle-shape table: breaks are markers on the tape and
a shape is named in the tooltip of the bar it describes, which is the only place it means anything.
Underneath sit two cards and then the working: **Around price** (`nearby.py` — every level from
every source, ranked by distance, with a count of how many independent sources name the same price)
and **What has actually paid** (every counted rate in the system in one table, each against its
baseline). The dense tables are still there, folded into `<details>` — folding is honest, deleting
would have made the page simpler by making it say less.

The split was not costing clarity, it was costing an answer: "what is above me and what is below
me" is a merge across all four structure modules, and no single tab could ever have shown it. That
is why `nearby.py` is a module and not a loop in the template. There is deliberately **no composite
strength score** on a level — touches, zone state and volume share are not in the same units, and
one number blending them would be a guess wearing a decimal point; the one derived number is
confluence, which is a count. The old Scorecard (every card, and how often it was right) is gone:
a page that grades its own cards in front of someone trying to read a market is a second thing to
read, and the per-card verdict badge already says what each card is claiming.

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
docker compose run --rm analyst backfill --days 365   # fill `bars` with past 15m candles, OI and funding
docker compose run --rm analyst backfill --interval 1m --days 90   # the base series every shorter frame folds from
#   both are resumable (they start from the newest bar stored) and idempotent (a stored bar is
#   never rewritten); --restart walks the whole window again. Holes are printed, never filled.
```

Tests run the engine in one process (`app.local_app`, inline evaluator, fake
exchange) so they need neither Docker nor Redis.

## Change Rules

- Keep live trading fail-closed.
- Parameterised SQL only.
- Never return a credential from an endpoint.
- Add a dependency only with a runtime owner.
- Every behaviour change ships with a test. Say so when behaviour changes.

## Working Rules

These bias toward caution over speed. For a trivial task, use judgement.

### Think before coding

Don't assume, don't hide confusion, surface tradeoffs.

- State assumptions explicitly; if uncertain, ask.
- If a request has more than one reading, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop, name what is confusing, and ask.

### Simplicity first

The minimum code that solves the problem, nothing speculative.

- No features beyond what was asked.
- No abstraction for single-use code.
- No flexibility or configurability nobody requested.
- No error handling for impossible cases.
- If it is 200 lines and could be 50, rewrite it.

The test: would a senior engineer call this overcomplicated? If yes, simplify.

### Surgical changes

Touch only what you must; clean up only your own mess.

- Don't "improve" adjacent code, comments or formatting.
- Don't refactor what isn't broken.
- Match the surrounding style, even where you would do it differently.
- Unrelated dead code gets mentioned, not deleted.
- Remove the imports, variables and functions YOUR change orphaned — and only those.

The test: every changed line traces directly to the request.

### Goal-driven execution

Define success, then loop until it is verified.

- "Add validation" → write tests for invalid input, then make them pass.
- "Fix the bug" → write a test that reproduces it, then make it pass.
- "Refactor X" → tests pass before and after.

For multi-step work, state the plan as steps with their checks:

```
1. [step] → verify: [check]
2. [step] → verify: [check]
```

Strong criteria let the work run independently; "make it work" needs constant
clarification. These rules are working if diffs carry fewer unnecessary changes,
fewer rewrites follow from overcomplication, and the clarifying question comes
before the implementation rather than after the mistake.
