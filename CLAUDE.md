# Engineering Guide

## System Purpose

TradeBuddy is an options desk for Delta Exchange India (BTC and ETH options). It opens
multi-leg structures — iron condor, iron butterfly, credit spreads, strangles, straddles —
automatically from the market's own numbers, and manually from the dashboard, on two independent
brokers: the built-in paper broker and Delta.

Two independent apps:

- `backend/` — FastAPI + the engine (market feed, trading, guard, auto-trader, AI, notifications),
  SQLite through SQLModel, timed jobs on APScheduler inside the engine. Port 8000.
- `frontend/` — Next.js 16 (App Router, TypeScript), Tailwind v4, shadcn/ui, SWR, recharts,
  lightweight-charts. Port 3000; it proxies `/api` to the backend (`next.config.ts`), and the live
  WebSocket connects to the backend's `/ws` directly.

**Paper is active and auto-trading by default. Delta is opt-in, on demo by default, and its
auto-trading is off at every engine start.** Runtime settings live in SQLite and are edited on the
Settings page; `.env` holds only process bootstrap and the LLM provider keys.

## Architecture

```
Delta WS (perp + every option ticker) ─┐
Delta REST (chain every 30s, bars 5m) ─┴─► MarketFeed ─► Desk (chain, regime, plan, assess)
                                                           │
   dashboard click ─► routes/trading ─┐                    │
   AutoTrader (every N min) ──────────┴─► TradingService.gate/open/close ─► Broker (paper | delta)
   Guard (5s: TP/SL/expiry, 30s: daily loss) ─► TradingService.close / settle_expired
   Analyst (LiteLLM) ◄─ market digest only ─ AutoTrader review, AI brief job
   EventHub ─► event log (SQLite), /ws dashboards, Notifier (Telegram, email)
```

- `app/engine.py` — wires every component once and owns the schedule. It is the only writer of
  trading state, settings and toggles, in one process: run the backend as a single worker.
- `app/core/` — `db.py` (SQLite, WAL), `models.py` (every table), `events.py` (`EventHub`: each
  subscriber has its own queue, so a slow notifier never delays anything), `scheduler.py`
  (APScheduler wrapper that records every run for the System page), `security.py` (PIN, sessions).
- `app/settings.py` — every runtime setting as one Pydantic model: ranges, choices and the
  fail-closed rules live here.
- `app/market/delta.py` — the only code that talks to Delta REST. `feed.py` — the only code that
  talks to the Delta WebSocket, plus the REST refresh that keeps the book complete and working when
  the socket is refused. `options.py` — quotes and chain arithmetic (ATM IV, skew, put/call, max
  pain, walls, implied move).
- `app/options/` — pure, no clock beyond what is passed, no network:
  `pricing.py` (payoff, exact max profit/loss and breakevens from the strike knots, POP and expected
  value under a driftless lognormal), `strategies.py` (catalog and builders: strikes by |delta| or
  ATM, wings a share of spot out, priced at bid for sales and ask for purchases), `regime.py`
  (volatility premium and direction → a rule score per strategy, every point with its reason),
  `assess.py` (a plan priced and checked: liquidity, credit/width, POP, **edge at realised vol**).
- `app/trading/service.py` — the only code that opens and closes structures. Bought legs fill first
  on entry and sold legs are bought back first on exit, so a short is never naked mid-way; a failed
  leg unwinds what filled. `desk.py` — the read side shared by the page and the auto-trader.
  `guard.py` — exits and the daily loss limit. `autotrader.py` — the decision cycle. `risk.py` —
  the trading day and halts.
- `app/brokers/` — a broker executes one leg (IOC limit) and reports its account; structures are the
  trading service's, recorded the same way for both brokers.
- `app/ai/llm.py` — LiteLLM: any provider by model id, primary then fallbacks in order.
  `analyst.py` — prompts, the allowlisted digest, answer shaping.
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
   the expiry rules.
8. **Absence over staleness.** A perpetual price older than 30s or an option quote older than 120s
   is no price.
9. **Secrets stay in the process.** No endpoint returns a key, secret or token; `Settings.public()`
   masks them; `SettingsChanged` carries field names only; LLM keys never enter the database.
10. **Private by default.** Every `/api` route and `/ws` needs the PIN session unless listed in
    `PUBLIC_PATHS`; `/ws` is checked before `accept()`.
11. **The model chooses, it never invents.** The AI reviewer may approve one listed candidate or
    skip; anything else is a skip, and every hard gate runs after it. What it is sent is
    `analyst.ALLOWED` — market numbers only, never a balance, position, order or P&L.
12. **Every decision is written down.** Each auto cycle is a `Decision` row with what it saw, every
    strategy it considered and why each was dropped, the model's answer, and what each broker did.
13. **Bounded.** Prices and ticks are never stored; events and briefs are pruned after 30 days,
    decisions after 90.

## Extension Points

### Strategy

Add a `StrategySpec` to `CATALOG` and a builder to `BUILDERS` in `options/strategies.py`, a score
in `options/regime.py`, and the key to `STRATEGY_KEYS`/`Strategy` in `settings.py`. A builder only
picks strikes; pricing, checks, sizing and execution are shared.

### Broker

Implement `Broker` (`brokers/base.py`): `account`, `execute` (IOC at the limit or refuse, idempotent
on `client_order_id`, `BrokerTimeout` when unsure), `lookup`, `settle`. Register it in
`Engine.brokers`, add it to `BROKERS` and a `<name>_active` / `auto_<name>` setting.

### Setting

A field on `Settings` with its range; it appears in `/api/settings` with its limits. Add the input
on the Settings page (`frontend/src/components/settings/sections.tsx`). If it changes where orders
go, add it to `DELTA_ROUTING_FIELDS`.

### Page

A folder under `frontend/src/app/(dashboard)/`, an entry in `components/layout/nav.ts`, reads via
`useApi()` (SWR), writes via `api()`. Live refresh: map the event type to the cache prefixes it
invalidates in `lib/live.tsx`.

## Commands

```bash
./start.sh                 # backend :8000 + dashboard :3000, hot reload
make install               # .venv with the backend, npm install for the dashboard
make check                 # ruff, eslint, tsc, pytest
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
