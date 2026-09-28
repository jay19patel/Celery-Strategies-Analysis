# TradeBuddy

Event-driven strategy signals and execution on Delta Exchange India, with a
built-in paper broker. One process: FastAPI dashboard + asyncio event bus +
one Delta WebSocket + SQLite.

## Quick start

```bash
make install
make run            # http://127.0.0.1:8080
```

Then, in the dashboard:

1. **Settings** → choose the broker: **Paper** (simulated) or **Delta Exchange**.
   For Delta, pick **Demo** or **Live**, paste the API key and secret, and
   press **Test connection**.
2. Switch **Trading** on in the top bar. It is off at every start and whenever
   the broker, account, keys or price feed change.
3. Turn strategies, and each strategy's symbols, on or off on **Strategies**.

No `.env` is needed. It only holds `HOST`, `PORT`, `DB_PATH` and `API_TOKEN`
(see `.env.example`).

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
| System | CPU, memory, loop lag, workers and queues, event rates, Delta REST latency |
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
                                 OrderRequested ─► Executor ─► active Broker
                                                               ├─ PaperBroker (simulated)
                                                               └─ DeltaBroker (REST, bracket SL/TP)
                          OrderPlaced | OrderFailed | OrderUnknown ─► look up by id, never resend
  orders, positions (private, Delta) ─► OrderUpdate, PositionUpdate
```

## Layout

```
tradebuddy/
  config.py        process bootstrap (host, port, db, token)
  settings.py      runtime settings, validated; stored in SQLite
  events.py        event types + EventBus
  delta.py         Delta REST client
  stream.py        Delta WebSocket, BarCloser, PriceBook
  runner.py        CandleClosed -> strategies -> SignalGenerated
  trading.py       Trader (gate) and Executor (orders)
  brokers/         Broker interface, PaperBroker, DeltaBroker
  store.py         SQLite schema and queries
  system.py        wiring, settings changes, dashboard views
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

Restart and it appears on the Strategies page. `ctx.market` also offers
`price(symbol)`, `candles(...)` and `option_chain(underlying)`.

Included: `random_1m`, `rsi_5m`, `ema_cross_15m`, `pcr_options`.

## Safety

- The default broker is **paper**; Delta defaults to the **demo** account.
- Becoming real-money (broker Delta + Live account) needs the confirmation
  phrase typed on the Settings page.
- Trading is off at every start and after any routing change. "Close all"
  turns it off and flattens the active broker.
- One order per strategy × symbol × bar (deterministic `client_order_id`).
- A symbol with an open position or an order in flight takes no new entry.
- A timeout is never resent: the order is looked up by its id.
- SL/TP go out with the entry (Delta bracket; paper watches every tick).
- No fresh WebSocket price → no trade.
- API keys are stored in the local database (`data/`, git-ignored) and never
  returned to the browser. Binding beyond localhost requires `API_TOKEN`.
