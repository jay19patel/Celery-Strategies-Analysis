# Crypto Strategy Analysis

A Celery-based crypto strategy engine with paper trading, optional Delta Exchange live execution, an operational FastAPI dashboard, SQLite persistence, and Redis-backed scheduling, caching, locks, and real-time updates.

## Architecture

```text
Celery Beat -> Redis broker -> Celery workers -> strategy tasks
                                      |             |
                                      |             +-> Delta Exchange market data
                                      |             +-> Redis OHLCV cache (DB 3)
                                      v
                              SQLite persistence
                                      |
FastAPI dashboard <-> Redis pub/sub <-+
```

- **SQLite** stores accounts, trades, positions, signals, batch results, and runtime configuration. It uses TRUNCATE journal mode for reliable locking on container-mounted volumes.
- **Redis DB 0** is the Celery broker, **DB 1** is the result backend, **DB 2** handles pub/sub and distributed locks, and **DB 3** caches OHLCV data.
- **Celery Beat** wakes every 10 seconds. The task itself applies the user-configured interval and an atomic Redis lock before dispatching a strategy chord.
- **ZeroMQ** is best-effort local telemetry only; it is not a durable trading-event transport.
- **Prometheus** can scrape `GET /metrics`; the custom dashboard continues to use cached JSON and WebSocket snapshots.

## Features

- Parallel strategy execution across configured symbols
- Paper accounts isolated by strategy and symbol
- Optional armed live-order execution through Delta Exchange India
- Shared, risk-managed portfolio simulation
- FastAPI dashboard with WebSocket status updates
- Runtime strategy, symbol, interval, risk, and broker configuration
- Health, Celery, Redis, SQLite, WebSocket, and telemetry monitoring

## Requirements

- Docker and Docker Compose, or Python 3.13+
- Redis 7+
- Delta Exchange credentials only when live execution is required

## Docker Setup

```bash
cp .env.example .env
docker compose up --build
```

The stack starts Redis, a Celery worker, Celery Beat, and the dashboard. Open `http://localhost:8080`.

Persistent application data is stored in `./data/stockanalysis.db`; Redis data uses the `redis_data` Docker volume.

## Local Development

```bash
uv sync --extra dev
docker compose up -d redis
uv run celery -A app.core.celery_app.celery_app worker --loglevel=INFO
uv run celery -A app.core.celery_app.celery_app beat --loglevel=INFO
uv run uvicorn frontend.main:app --reload --port 8080
```

Run the test suite and lint checks with:

```bash
uv run pytest
uv run ruff check .
```

## Configuration

Configuration is read from environment variables and selected pipeline settings can be changed from the dashboard and persisted in SQLite.

| Variable | Default | Purpose |
|---|---:|---|
| `SQLITE_DB_PATH` | `data/stockanalysis.db` | SQLite database path |
| `REDIS_BROKER_URL` | `redis://localhost:6379/0` | Celery broker |
| `REDIS_RESULT_URL` | `redis://localhost:6379/1` | Celery result backend |
| `REDIS_PUBSUB_URL` | `redis://localhost:6379/2` | Pub/sub and lock client |
| `SYMBOLS` | `BTC-USD,ETH-USD,SOL-USD` | Comma-separated symbols |
| `STRATEGIES` | `*` | Strategy class paths, or automatic discovery |
| `SCHEDULE_SECONDS` | `60` | Batch interval; minimum 10 seconds |
| `EXECUTION_MODE` | `PAPER` | `PAPER` or `LIVE` |
| `LIVE_TRADING_ARMED` | `false` | Explicit live-order safety gate |
| `DELTA_API_KEY` | empty | Delta Exchange API key |
| `DELTA_API_SECRET` | empty | Delta Exchange API secret |
| `DELTA_CLIENT_ID` | `0` | Delta Exchange client ID |

See [app/core/settings.py](app/core/settings.py) for all risk and portfolio settings.

## Manual Execution

```python
from app.core.tasks import run_all_batch_task

result = run_all_batch_task.delay()
print(result.get())
```

Pass `force=True` only for an intentional manual run that should bypass the configured interval.

## Adding A Strategy

Create one module under `app/strategies/` containing a concrete `BaseStrategy` subclass. With `STRATEGIES=*`, it is discovered automatically; no registry or package import list needs editing. To select strategies explicitly, provide comma-separated dotted class paths in `STRATEGIES`.

## Adding A Data Provider

Create a `BaseDataProvider` subclass and set `DATA_PROVIDER` to its dotted class path:

```text
DATA_PROVIDER=my_package.market_data.CustomProvider
```

The factory validates the interface at startup. Built-in short names such as `delta` remain supported.

## Operations

```bash
docker compose ps
docker compose logs -f worker
docker compose logs -f beat
docker compose logs -f dashboard
docker exec -it stockanalysis-redis redis-cli
```

The dashboard exposes system health and runtime controls at `http://localhost:8080`. Live trading remains disabled until credentials are configured and the explicit arming flow succeeds.

### Observability

The dashboard uses `/ws/live` for one-second market ticks and five-second operational snapshots. When the socket is disconnected, it falls back to `GET /api/system/metrics`. Both paths reuse the background health collector cache, so browser traffic does not trigger repeated Celery cluster inspections.

Prometheus can scrape the standard endpoint:

```yaml
scrape_configs:
  - job_name: tradebuddy
    scrape_interval: 15s
    static_configs:
      - targets: ["dashboard:8080"]
```

Use `/api/system/metrics` or `/ws/live` in custom frontend code. Use `/metrics` for Prometheus, Grafana, and alerting rather than parsing Prometheus text in the browser.

## License

MIT
