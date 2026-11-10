# Engineering Guide

## System Purpose

TradeBuddy trades Delta Exchange India (BTC, ETH, XAUT): defined-risk option structures (iron
condor, iron butterfly, credit spreads, the expiry decay spread) and positions on the perpetuals,
automatically from strategy rules and manually from the dashboard, on two independent brokers: the
built-in paper broker and Delta. **Every strategy is one file in `backend/app/strategies/`.**

Two independent apps:

- `backend/` — FastAPI + the engine (market feed, trading, guard, auto-trader, AI, notifications),
  SQLite through SQLModel, timed jobs on APScheduler inside the engine. Port 8000.
- `frontend/` — Next.js 16 (App Router, TypeScript), Tailwind v4, shadcn/ui, SWR, recharts,
  lightweight-charts. Port 3000; it proxies `/api` to the backend (`next.config.ts`), and the live
  WebSocket connects to the backend's `/ws` directly.

**Paper is active and auto-trading by default. Delta is opt-in, on demo by default, and its
auto-trading is off at every engine start; a strategy reaches Delta only with its own `live` switch.**
Runtime settings (33) live in SQLite and are edited on the Settings page; each strategy's
own numbers are its `Params`, edited on the Strategies page. `.env` holds only process bootstrap and
the LLM provider keys. No Celery, Redis or Postgres: one process, one writer (`docs/review/04_target_architecture.md`).

## Architecture

```
Delta WS (tickers, every trade, book) ─┐
Delta REST (chain every 30s, bars 5m) ─┴─► MarketFeed ─► Desk (chain, regime, plan, assess)
                                            └─ Tape ─► FlowBar (1m footprint) ─► MarketDesk (flow, gex, bias)
                                                           │
   strategies/*.py (auto-discovered by registry.py)        │
     ├─ perp:    StrategyRunner (20s, closed bars) ────────┤
     └─ options: AutoTrader (15 min, per strategy expiry) ─┤
   dashboard click ─► routes/trading ─────────────────────┴─► TradingService.gate (+ risk.py) / open / close ─► Broker (paper | delta)
   Guard (5s: TP/SL/trail/expiry, 30s: daily loss + drawdown stop) ─► TradingService.close / settle_expired
   Reconciler (5 min) ─► Delta positions vs the desk; a mismatch blocks Delta entries
   Analyst (LiteLLM) ◄─ market digest only ─ AI brief job (it never decides a trade)
   EventHub ─► event log (SQLite), /ws dashboards, Notifier (Telegram, email)
```

- `app/engine.py` — wires every component once and owns the schedule. It is the only writer of
  trading state, settings and toggles, in one process, enforced by a file lock beside the database
  (a second engine refuses to start). At start it recovers structures a crash left `opening`/`closing`.
- `app/core/` — `db.py` (SQLite, WAL, incremental auto-vacuum; `maintain` hourly), `models.py` (every table),
  `logstore.py` (every WARNING/ERROR as one counted event-log row per message per 10 minutes), `events.py` (`EventHub`: each
  subscriber has its own queue, so a slow notifier never delays anything), `scheduler.py`
  (APScheduler wrapper that records every run for the System page), `security.py` (PIN, sessions).
- `app/settings.py` — every runtime setting as one Pydantic model: ranges, choices and the
  fail-closed rules live here. `strategies` holds each strategy's `StrategyConfig` (enabled, live,
  underlyings, params that differ from its defaults); `from_stored` migrates older settings.
- `app/market/delta.py` — the only code that talks to Delta REST. `feed.py` — the only code that
  talks to the Delta WebSocket, plus the REST refresh that keeps the book complete and working when
  the socket is refused; it cleans every bar (`clean_candles`: a bad print is dropped or its wick
  trimmed). `tape.py` — the trade tape and the book folded into one footprint minute per symbol,
  and option premium by kind and side. `options.py` — quotes and chain arithmetic (ATM IV, skew,
  put/call, max pain, walls, implied move).
- `app/analysis/` — `flow.py` (delta, CVD, imbalances, absorption, exhaustion, delta profile from the
  tape), `bias.py` (the day's bias and style, every point with its reason), `service.py`
  (`MarketDesk`: every reading, cached, and the jobs that record the options minute and the bias).
- `app/options/` — pure, no clock beyond what is passed, no network:
  `pricing.py` (payoff, exact max profit/loss and breakevens from the strike knots, POP and expected
  value under a driftless lognormal), `strategies.py` (the `Plan`, the strike pickers, a hand-built
  structure; priced at bid for sales and ask for purchases), `regime.py` (volatility premium and
  direction, and the score helpers strategies compose), `assess.py` (a plan priced and checked:
  liquidity, credit/width, POP, **edge at realised vol** — missing realised vol fails it), `decay.py`
  (the decay spread scan), `gex.py`, `ideas.py` (single-option ideas).
- `app/strategies/` — **one file per strategy**. `base.py` defines `Strategy` (perpetual: pure
  `decide()` on closed bars) and `OptionStrategy` (`expiry()`, `score()`, `pick()`/`build()`), each
  with a Pydantic `Params`. `registry.py` imports every module not starting with `_` and registers
  each concrete subclass by `name`. `_auction.py` and `_options.py` hold shared helpers; `_template.py`
  is the copy-me file. The runner refuses any perp signal against a day bias of 0.5 or stronger.
- `app/trading/service.py` — the only code that opens and closes structures. Bought legs fill first
  on entry and sold legs are bought back first on exit, so a short is never naked mid-way; a failed
  leg unwinds what filled. `desk.py` — the read side shared by the page and the auto-trader.
  `guard.py` — exits, the daily loss limit and the drawdown stop. `autotrader.py` — the option
  strategies' decision cycle. `runner.py` — the perpetual strategies. `risk.py` — the trading day,
  halts, the kill switch, reconciliation blocks and the portfolio rules. `reconcile.py` — Delta's
  positions against the desk. `edge.py` — each strategy's measured edge in R and the size it earns.
- `app/brokers/` — a broker executes one leg (IOC limit) and reports its account; structures are the
  trading service's, recorded the same way for both brokers.
- `app/ai/llm.py` — LiteLLM: any provider by model id, primary then fallbacks in order.
  `analyst.py` — the market brief: prompts, the allowlisted digest, answer shaping.
- `app/notify/` — Telegram and email, fed by events; the daily report.

## Invariants

Enforced by tests (`backend/tests`). Breaking one should fail the build.

1. **Fail closed.** Delta is inactive and on demo by default. Real money needs
   `LIVE_CONFIRM_PHRASE`. Delta auto-trading is off at every start and after any change to the Delta
   account or keys.
2. **One structure per broker × request.** The structure id is derived from the broker and the
   request id (a dashboard ticket, or `auto-<decision id>`); a retry opens nothing twice. Each leg
   order's `client_order_id` derives from the structure.
3. **Timeout is not rejection.** A `BrokerTimeout` is looked up by `client_order_id`, never resent.
   Unresolved, the structure is `error` and that broker opens nothing new until it is closed.
4. **Never naked mid-way.** Entry fills bought legs first; exit buys back sold legs first, and a
   short that cannot be bought back stops the sale of its protection. A failed entry unwinds.
5. **Brokers are independent.** Every gate is per broker; one halting never touches another.
6. **The broker decides.** Before an entry the account is read; if that fails, no trade.
7. **Exits read what closing would book** — bids for longs, asks for shorts, every fee
   (`TradingService.valuation`). Without a fresh quote on every leg, the guard decides nothing but
   the expiry rules. A stop holds `STOP_CONFIRM_CHECKS` (3) checks in a row unless the spot is past a
   breakeven. A defined-risk credit (spread, condor, butterfly, decay spread) is held to settlement —
   no stop, take profit or time exit: its worst case is its wings and it was sized on that. On Delta a
   perpetual's stop is also left with the exchange at entry; if it cannot be placed the position is closed.
8. **Absence over staleness.** A perpetual price older than 30s or an option quote older than 120s
   is no price; nor is a pulled book — an option side further from its mark than 60% of it and 0.05%
   of spot (`options.fair_side`), a perpetual side 2% from its mark. A strategy stopped out on an
   underlying is not re-entered there the same trading day, options and perpetuals alike.
9. **Secrets stay in the process.** No endpoint returns a key, secret or token; `Settings.public()`
   masks them; `SettingsChanged` carries field names only; LLM keys never enter the database.
10. **Private by default.** Every `/api` route and `/ws` needs the PIN session unless listed in
    `PUBLIC_PATHS`; `/ws` is checked before `accept()`, on the session cookie or a one-time,
    60-second ticket from `/api/auth/ws` (a dashboard on another host sends no cookie to it).
11. **The model writes, it never trades.** The AI writes the market brief and nothing else: every
    trade is decided by the rules and their hard gates. What it is sent is `analyst.ALLOWED` —
    market numbers only, never a balance, position, order or P&L.
12. **Every attempt is written down.** Each option cycle that reaches a broker is a
    `Decision` row with what it saw, every strategy it considered and why each was dropped,
    and what each broker did. A skip before any broker is not stored.
13. **Bounded, for years of uptime.** Ticks are never stored: the tape is kept as one footprint
    minute per symbol (`FlowBar`, 30 days). Bars (15m a year, 1m 30 days), option-chain snapshots
    (`data/chains/`, 90 days) and the options minute (30 days) are kept for backtests; the day's bias
    two years; events and briefs 30 days, warnings and errors 90, decisions 90. No in-memory cache
    is keyed by something that grows forever (an option listing, a client address); the Delta product
    cache is bounded. A skip is shown live and never stored. Market history is stored only from the
    live exchange: testnet prints never enter it.
14. **No unbounded loss on real money.** A structure without a bounded max loss is refused on Delta
    live; elsewhere it is sized on a move of three expected moves, not on its stop.
15. **Size follows a measured edge.** Every fee is charged with India's 18% GST. Automatic entries
    risk `risk_per_trade_pct` times `TradingService.risk_scale`: a strategy proven losing over 30+
    trades (its whole 95% range in R below zero) gets nothing, one proven winning a quarter Kelly on
    the low end of the range (1-2x), and on Delta live anything not yet proven on paper gets nothing.
    A drawdown of 10% from the broker's equity peak halves the risk, 20% quarters it, and
    `max_drawdown_pct` (25%) stops every entry and closes every position until resumed by hand.
16. **Bars are fitted to the mark.** A stored traded bar never leaves its mark-price bar's range by
    more than `MARK_BAND`: Delta prints junk on the ETH perpetual and revises it out later. A tape
    print is judged against the mark, so a junk print never becomes the yardstick.
17. **One engine, recovered.** A second engine on the same database refuses to start. A structure a
    crash left `opening` or `closing` goes to `error` at start, its recorded fills on its legs and its
    unanswered orders marked unknown, so the guard looks them up and gets it flat.
18. **Stop all stops.** The kill switch (`risk.kill_state`, in the database) refuses every entry on
    every broker, manual included, until resumed; then every position is closed.
19. **The portfolio caps automatic risk.** Per broker, automatic entries hold one position per
    underlying across every family, and their worst cases stay within `MAX_HEAT_PCT` (4%) of equity.
20. **The desk matches the exchange.** Delta positions are reconciled with open structures at start
    and every 5 minutes; a mismatch blocks Delta entries until they agree. It never trades to fix one.

## Extension Points

### Strategy

One file in `backend/app/strategies/` — nothing else. Follow `docs/ADDING_A_STRATEGY.md` (or the
`add-strategy` project skill): copy `_template.py`, put its numbers in `Params`, test that it fires
and refuses, backtest it, `make check`. It starts off and on paper. Delete the file to remove it.

### Broker

Implement `Broker` (`brokers/base.py`): `account`, `leverage`, `execute` (IOC at the limit or refuse,
idempotent on `client_order_id`, `BrokerTimeout` when unsure), `lookup`, `settle`, and `protect`/`unprotect`
(a reduce-only stop held by the exchange; return "" when the broker holds none). Register it in
`Engine.brokers`, add it to `BROKERS` and a `<name>_active` / `auto_<name>` setting.

### Setting

Only if the owner can make the decision well and no strategy owns it (then it is that strategy's
`Params`). A field on `Settings` with its range; it appears in `/api/settings` with its limits. Add
the input on the Settings page (`frontend/src/components/settings/sections.tsx`), under `Advanced`
unless most owners need it. If it changes where orders go, add it to `DELTA_ROUTING_FIELDS`.

### Page

A folder under `frontend/src/app/(dashboard)/`, an entry in `components/layout/nav.ts`, reads via
`useApi()` (SWR), writes via `api()`. Live refresh: map the event type to the cache prefixes it
invalidates in `lib/live.tsx`.

## Commands

```bash
./start.sh                 # backend :8000 + dashboard :3000, hot reload
./start_backend.sh         # backend only (production mode, single worker)
make install               # .venv with the backend, npm install for the dashboard
make check                 # ruff, eslint, tsc, pytest
cd backend && ../.venv/bin/python -m app.backtest.perp trend_4h_short --underlying BTC   # perp backtest on stored bars (live exits)
```

Tests run the engine's parts over a real captured option chain (`tests/fixtures/btc_chain.json`)
with a stub REST client: no network, no running server.

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
