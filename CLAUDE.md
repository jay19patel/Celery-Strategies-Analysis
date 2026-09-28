# Engineering Guide

## System Purpose

TradeBuddy turns strategy signals into orders on a pluggable broker: the
built-in paper broker or Delta Exchange India. One process: FastAPI + an
asyncio event bus + one Delta WebSocket + SQLite. No Celery, no Redis.

**Paper is the default broker, Delta defaults to demo, and trading is off at
every start.** Runtime settings live in SQLite and are edited on the Settings
page; `.env` only holds process bootstrap (host, port, db path, API token).

## Architecture

Event-driven. Components talk through `EventBus` (`tradebuddy/events.py`).
Each subscriber has its own queue and worker, so it handles events in order and
never blocks another subscriber.

```
DeltaStream ─ Tick ─────────► PriceBook, PaperBroker (protective exits)
BarCloser ─── CandleClosed ─► StrategyRunner ─ SignalGenerated ─► Trader
                                            OrderRequested ◄──────┘ (or TradeSkipped)
                                                  ▼
                                    Executor ─► brokers[order.broker]
                                                  ▼
                                    OrderPlaced | OrderFailed | OrderUnknown
DeltaStream (private) ─ OrderUpdate ─► Executor
every event (except Tick) ─► Store.events;  every event ─► dashboard /ws
```

- `brokers/base.py` — the `Broker` protocol. `PaperBroker` and `DeltaBroker` implement it.
- `delta.py` — the only code that talks to Delta REST.
- `stream.py` — the only code that talks to the Delta WebSocket.
- `trading.py` — the only code that decides to trade (`Trader`) and sends orders (`Executor`).
- `settings.py` — validation for every runtime setting; `System._apply` applies them.
- `strategies/` — signal logic only.

## Invariants

Enforced by tests. Breaking one should fail the build.

1. **Fail closed.** Paper by default; Delta demo by default. Becoming
   real-money needs `LIVE_CONFIRM_PHRASE`. Trading resets to off on every start
   and on any change to broker, account, keys or price feed.
2. **One order per strategy × symbol × bar.** `client_order_id` is derived from
   them and is the orders table primary key. Paper is idempotent on it too.
3. **Timeout is not rejection.** `BrokerTimeout` is looked up by
   `client_order_id`, never resent. Unresolved, the symbol stays blocked on
   that broker.
4. **Orders stay with their broker.** The broker is fixed when the order is
   reserved; switching brokers never reroutes or unblocks it.
5. **Closed candles only.** Strategies run on `CandleClosed`; a strategy with
   `lookback` must see the bar that closed.
6. **The broker decides.** Before an entry, open positions are read from the
   broker; if that fails, no trade.
7. **Protected entries.** SL/TP go with the entry (Delta bracket; paper
   checks every tick, plus liquidation and max hold).
8. **Absence over staleness.** A WebSocket price older than 30s is no price.
9. **Secrets stay in the process.** No endpoint returns an API key or secret;
   `Settings.public()` masks them and `SettingsChanged` carries field names only.
10. Every refusal to trade is a `TradeSkipped` event with its reason.

## Extension Points

### Strategy

One module in `tradebuddy/strategies/` with a concrete `Strategy` subclass; it
is discovered automatically. Keep `name` stable, bump `version` when logic
changes. Strategies return a `Signal` or `None`; they never place orders.

### Broker

Implement `Broker` (`brokers/base.py`), register it in `System.brokers`, and
add its name to `CHOICES["broker"]` in `settings.py`. It must be idempotent on
`client_order_id` and raise `BrokerTimeout` (not `BrokerError`) when the
outcome is unknown.

### Setting

Add a field to `Settings`, a range or choice in `settings.py`, and an input on
`templates/settings.html`. If it changes where orders or prices go, add it to
`ROUTING_FIELDS`.

### Page

A template extending `base.html`, an entry in `PAGES` in `app.py`, and JSON
routes that call `System`. State-changing routes take `Depends(protected)`.
Live updates come from `TB.on(eventType, fn)`.

## Commands

```bash
make install
make run      # http://127.0.0.1:8080
make check    # lint + tests
make token
```

## Change Rules

- Keep live trading fail-closed.
- Parameterised SQL only.
- Never return a credential from an endpoint.
- Add a dependency only with a runtime owner.
- Every behaviour change ships with a test. Say so when behaviour changes.
