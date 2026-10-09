# TradeBuddy

Event-driven strategy signals and execution. Two independent brokers — the
built-in paper broker and Delta Exchange India — run either alone or together
on the same signals. One process: FastAPI dashboard + asyncio event bus +
one Delta WebSocket + SQLite.

## Quick start

```bash
docker compose up
```

Open **http://127.0.0.1:8080**. Stop with `Ctrl+C` (or `docker compose up -d`
to run in the background, `docker compose down` to stop). Every `up` rebuilds
the image, so code changes are picked up. More strategy workers:
`docker compose up --scale worker=4`.

Then, in the dashboard:

1. **Paper** is active and trading out of the box.
2. To add Delta: **Settings** → tick **Delta Exchange**, pick **Demo** or
   **Live**, paste the API key and secret, **Test connection**, **Save**.
   Untick **Paper trading** if you want Delta only.
3. Each active broker has its own switch in the top bar. Delta's is off at
   every start and whenever its account or keys change.
4. Turn strategies, and each strategy's symbols, on or off on **Strategies**.

Settings, orders and paper trades live in `./data`, so they survive
restarts. No `.env` is needed; copy `.env.example` to `.env` only to set an
`API_TOKEN` that protects every change from the dashboard.

## What runs

| Container | Does |
|---|---|
| `feed` | Delta WebSocket, candle closes → ZeroMQ |
| `engine` | trading decisions, paper + Delta brokers, SQLite |
| `worker` | Celery: evaluates strategies (scale it) |
| `web` | the dashboard, talking to the engine over ZeroMQ |
| `redis` | Celery's broker |

```
                 ┌──────────── ZeroMQ PUB (Tick, CandleClosed, Delta private) ────────────┐
Delta WS ─► [feed]                                                                        ▼
               ▲                                                        [engine]  Trader · Executor · PaperBroker · DeltaBroker · SQLite
               └── SettingsChanged ── ZeroMQ PUB (every event) ◄───────   │   ▲
                                              │                           │   │ ZeroMQ PUSH (StrategyEvaluated)
                                              ▼                  Celery (Redis)│
                                            [web] ◄─ ZeroMQ RPC ─►        ▼   │
                                          dashboard                  [worker × N]  strategies
```

Only the engine decides and sends orders, so there is one writer and no
locks. Workers only evaluate strategies; a result that arrives more than a
bar late is dropped, and a task Celery delivers twice cannot open a second
order (same `client_order_id`). ZeroMQ is not durable, which is fine for
prices and notifications: orders never depend on it.

## Pages

| Page | What it shows |
|---|---|
| Overview | equity, P&L, signal/order counts, paper equity curve, live prices, strategies, recent activity |
| Strategies | per-strategy and per-symbol switches, runs, signals, errors, last result |
| Signals | every signal and why it did or did not trade, filterable |
| Positions | open positions per broker, close, edit paper SL/TP, resting SL/TP legs |
| Orders | every order the engine sent, per broker, with status |
| Account | balance, available, margin, P&L per broker |
| Paper Trading | equity curve, win rate, profit factor, drawdown, per-strategy stats, trade history, reset |
| Market Data | live price charts, WebSocket channels, what closed each candle |
| System | processes, Celery workers, CPU, memory, loop lag, bus queues, event rates, Delta REST latency |
| Event Log | every event on the bus, live, filterable |
| Settings | broker, Delta account and keys, price feed, risk, paper account |

Every page updates live over the dashboard WebSocket (`/ws`).

## How it flows

```
Delta WebSocket
  v2/ticker ─────────────► Tick ─────────► PriceBook · PaperBroker (SL/TP/liquidation on every tick)
  candlestick_1m/5m/15m ─► CandleClosed ─► StrategyRunner ─► SignalGenerated
  (or the clock, if the stream is quiet)                            │
                                                                    ▼
                         Trader: trading on? strategy on? pair on? broker ready?
                                 live price? nothing in flight? no open position?
                                      │ no ─► TradeSkipped(reason)
                                      ▼ yes
                  one per active broker: OrderRequested ─► Executor ─► PaperBroker (simulated)
                                                                     └► DeltaBroker (REST, bracket SL/TP)
                          OrderPlaced | OrderFailed | OrderUnknown ─► look up by id, never resend
  orders, positions (private, Delta) ─► OrderUpdate, PositionUpdate
```

## Layout

```
tradebuddy/
  __main__.py      python -m tradebuddy {feed|engine|web|worker} (docker-compose runs these)
  roles.py         what each process runs
  config.py        process bootstrap: host, port, db, token, ZeroMQ, Celery (set by docker-compose)
  settings.py      runtime settings, validated; stored in SQLite
  events.py        event types + in-process EventBus
  codec.py         events <-> JSON across processes
  transport.py     ZeroMQ: pub/sub, push/pull, RPC
  worker.py        Celery app, the evaluation task, CeleryEvaluator
  runner.py        CandleClosed -> jobs -> StrategyEvaluated -> SignalGenerated
  trading.py       Trader (per-broker gate) and Executor (orders)
  brokers/         Broker interface, PaperBroker, DeltaBroker
  delta.py         Delta REST client
  stream.py        Delta WebSocket, BarCloser, PriceBook
  store.py         SQLite schema and queries
  system.py        the engine: wiring, settings changes, views
  api.py           everything the dashboard can call (local or over RPC)
  app.py           pages + JSON API + /ws
  templates/       Jinja2 pages
  static/          CSS and JS
  strategies/      one file per strategy, auto-discovered
```

## Writing a strategy

Add a file to `tradebuddy/strategies/`:

```python
"""Close above EMA20 on 5-minute candles."""
from tradebuddy.strategies.base import Context, Signal, Strategy
from tradebuddy.strategies.indicators import ema

class MyStrategy(Strategy):
    name = "my_strategy"        # stable: toggles and history key off it
    version = 1                 # bump when the logic changes
    interval = "5m"             # runs when a 5m candle closes
    symbols = ("BTCUSD", "ETHUSD")
    size = 1                    # contracts per trade
    lookback = 50               # closed candles in ctx.candles

    async def on_candle(self, ctx: Context) -> Signal | None:
        closes = [c.close for c in ctx.candles]
        if closes[-1] > ema(closes, 20)[-1]:
            return Signal("buy", "close above EMA20")
        return None
```

`docker compose up` again (it rebuilds) and it appears on the Strategies page. `ctx.market` also offers
`price(symbol)`, `candles(...)` and `option_chain(underlying)`.

Included: `ema_9_15_15m`, `ema_cross_15m`, `mother_candle_15m`, `mother_candle_1h`, `pcr_options`, `tb_master_15m`.

## Safety

- Paper is the default; Delta is opt-in and defaults to the **demo** account.
- Becoming real-money (Delta active + Live account) needs the confirmation
  phrase typed on the Settings page.
- Delta trading is off at every start and after any Delta account or key
  change. "Close all" switches every broker off and flattens each one.
- One order per broker × strategy × symbol × bar (deterministic `client_order_id`).
- Brokers gate independently: a stuck Delta order never blocks paper.
- A symbol with an open position or an order in flight takes no new entry.
- A timeout is never resent: the order is looked up by its id.
- SL/TP go out with the entry (Delta bracket; paper watches every tick).
- No fresh WebSocket price → no trade.
- API keys are stored in the local database (`data/`, git-ignored) and never
  returned to the browser. The dashboard is published on 127.0.0.1 only;
  set `API_TOKEN` in `.env` to require a token for every change.
