# Engineering Guide

## System Purpose

TradeBuddy turns strategy signals into orders on pluggable brokers: the
built-in paper broker and Delta Exchange India, independently, either or both.

It runs as containers under `docker compose up`: feed · engine · web ·
Celery workers, joined by ZeroMQ, with Redis as the Celery broker.

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
        ── PUB every event ──► web (dashboard /ws), feed (SettingsChanged)
        ◄─ RPC (api.METHODS) ── web
worker  Celery task: evaluate() one strategy on one bar, PUSH the result
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
- `trading.py` — the only code that decides to trade (`Trader`) and sends orders (`Executor`).
- `settings.py` — validation for every runtime setting; `System._apply` applies them.
- `strategies/` — signal logic only.

## Invariants

Enforced by tests. Breaking one should fail the build.

1. **Fail closed.** Delta is inactive and on demo by default. Becoming
   real-money needs `LIVE_CONFIRM_PHRASE`. `trading:delta` resets to off on
   every start and on any change to the Delta account or keys.
2. **One order per broker × strategy × symbol × bar.** `client_order_id` is derived from
   them and is the orders table primary key. Paper is idempotent on it too.
3. **Timeout is not rejection.** `BrokerTimeout` is looked up by
   `client_order_id`, never resent. Unresolved, the symbol stays blocked on
   that broker.
4. **Brokers are independent.** A signal is gated per broker; the broker is
   fixed when the order is reserved; one broker's state never blocks another.
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
11. **Late is stale.** A strategy result that finishes more than a bar after
    its bar closed is dropped; Celery tasks expire after one bar.
12. **ZeroMQ is not durable.** Nothing that must survive a restart travels
    only over ZeroMQ; ZeroMQ endpoints stay on loopback or a private network.

## Extension Points

### Strategy

One module in `tradebuddy/strategies/` with a concrete `Strategy` subclass; it
is discovered automatically. Keep `name` stable, bump `version` when logic
changes. Strategies return a `Signal` or `None`; they never place orders.

### Broker

Implement `Broker` (`brokers/base.py`), register it in `System.brokers`, add
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
routes that call `System`. State-changing routes take `Depends(protected)`.
Live updates come from `TB.on(eventType, fn)`.

## Commands

```bash
docker compose up          # everything: http://127.0.0.1:8080 (rebuilds on every up)
make install && make check # local lint + tests
make token                 # an API_TOKEN for .env
```

Tests run the engine in one process (`app.local_app`, inline evaluator, fake
exchange) so they need neither Docker nor Redis.

## Change Rules

- Keep live trading fail-closed.
- Parameterised SQL only.
- Never return a credential from an endpoint.
- Add a dependency only with a runtime owner.
- Every behaviour change ships with a test. Say so when behaviour changes.
