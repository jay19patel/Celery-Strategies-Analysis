# Engineering Guide

## System Purpose

TradeBuddy is a crypto strategy-analysis and trading system. Celery executes scheduled strategies, SQLite stores operational state, Redis provides task transport, locks, pub/sub, and market-data caching, and FastAPI serves the custom dashboard. Paper execution is the default; Delta Exchange live execution requires credentials and explicit arming.

## Runtime Boundaries

- `app/core/`: scheduling, strategy execution, health collection, logging, and portfolio simulation.
- `app/services/`: business operations consumed by API routers and WebSocket snapshots.
- `app/database/`: SQLite persistence and Redis messaging.
- `app/data_providers/`: provider interface, factory, and exchange implementations.
- `app/strategies/`: independently discoverable `BaseStrategy` implementations.
- `app/broker/`: paper/live execution and Delta Exchange integration.
- `frontend/`: FastAPI routers, WebSocket stream, and static dashboard.

Routers must call services rather than embedding business logic. Strategies and tasks use the provider-neutral `app.utility.data_provider` facade. Trading state belongs in SQLite; Redis data is transient.

## Extension Points

### Strategy

Add one module under `app/strategies/` with a concrete `BaseStrategy` subclass. `STRATEGIES=*` discovers it automatically. Do not edit `app/strategies/__init__.py`. Keep `StrategyResult.strategy_name` stable because paper accounts are keyed by `(strategy_name, symbol)`.

### Data Provider

Implement `BaseDataProvider` and set `DATA_PROVIDER` to its dotted class path, for example `my_package.market.CustomProvider`. The factory validates the interface. Built-in aliases such as `delta` remain supported.

### API Feature

Put domain work in an `app/services/` class, expose it through a small router under `frontend/routers/`, and add endpoint plus service tests. Do not query SQLite or external brokers directly from browser-facing route functions.

## Observability

- Application and audit logs use the centralized logger and professional `event_name key=value` messages.
- `/api/system/metrics` and `/ws/live` serve the custom frontend from the shared health cache.
- `/metrics` exposes numeric Prometheus metrics for scraping, history, and alerts.
- Prometheus is not a log store. Exceptions, order decisions, and audit details remain in structured application logs.
- ZeroMQ is best-effort local telemetry only, never a durable trading-event channel.

## Scheduling

- `run_all_batch_task` gates the dynamic interval with a Redis lock, builds a Celery chord, and aggregates strategy results.
- `run_portfolio_task` advances the shared risk-managed portfolio simulation under its own Redis lock.
- Protective paper exits run every batch even when strategies return `HOLD`.

## Commands

```bash
uv sync --extra dev
uv run pytest
uv run ruff check --select E9,F63,F7,F82 app tests
docker compose up --build
```

Dashboard: `http://localhost:8080`

## Change Rules

- Keep live trading fail-closed and preserve explicit arming.
- Use parameterized SQL and table-aware repository methods.
- Avoid compatibility shims for technologies no longer present.
- Do not place benchmark, random-signal, or artificial-delay strategies in the production strategy package.
- Add dependencies only when they have a runtime owner and remove their stale code paths together.
