# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A crypto (ETHUSD/BTCUSD) signal-generation + paper-trading system: Celery workers run trading
strategies on a schedule, a `PaperBroker` simulates trades against the signals, and a FastAPI
dashboard reads the results straight out of MongoDB. Nothing here places real orders — it's all
paper trading, meant to validate strategies before they'd inform real trades.

## Commands

Local dev (no Docker):
```bash
uv sync                                                          # install deps into .venv
uv run celery -A app.core.celery_app.celery_app worker --loglevel=INFO
uv run celery -A app.core.celery_app.celery_app beat --loglevel=INFO
./dashboard.sh                                                    # dashboard at http://127.0.0.1:8000, opens browser
```

Docker (mirrors production deployment):
```bash
docker compose up --build -d      # redis, mongo, worker, beat, dashboard (port 8080), flower (port 5555)
docker compose logs -f worker     # container names: stockanalysis-{redis,mongo,worker,beat,dashboard,flower}
```

Tests: `pytest` is **not currently a declared dependency** (missing from `pyproject.toml` and the
lockfile) — install it first: `uv add --dev pytest`, then `uv run pytest tests/ -q`. Only one test
file exists (`tests/unit/test_paper_broker.py`) and it's stale: it calls `PaperBroker._get_account(strategy_name)`
with one argument, but the current signature is `_get_account(strategy_name, symbol)` (accounts are
keyed per `(strategy, symbol)` pair, not per strategy) — expect it to fail until updated.

No lint/format tooling is configured in this repo (no ruff/black/flake8 config).

## Architecture

### Two independent Celery schedules — don't confuse them

`app/core/celery_app.py` registers two unrelated periodic tasks, each with its own paper-trading
model. A lot of confusion is avoidable just by keeping these separate in your head:

**1. Batch strategy pipeline** — `run_all_batch_task`, every `SCHEDULE_SECONDS` (default 600s).
`app/core/tasks.py: trigger_batch_execution` builds `symbols × strategies` task signatures
(`StrategyManager`) and fires them as a Celery chord: each `execute_strategy_task` instantiates one
`BaseStrategy` subclass from `app/strategies/` and calls `.execute(symbol)` → a `StrategyResult`.
The chord callback `process_batch_results` aggregates results, runs `PaperBroker.check_protective_exit`
on every (strategy, symbol) pair **every cycle regardless of signal** (so a stop/target isn't missed
while a strategy is signaling HOLD), publishes non-HOLD signals to Redis pub/sub + `signals_log`, and
calls `PaperBroker.process_signal` to actually open/close paper positions.

**2. Portfolio simulation pipeline** — `run_portfolio_task`, every `portfolio_schedule_seconds`
(default 1200s), ETHUSD only. `app/core/portfolio_tasks.py` fetches one rolling OHLCV window, builds
`app/utility/features.py`'s entire `STRATEGIES` dict directly into `+1/-1/0` direction arrays
(bypassing `BaseStrategy` entirely), and feeds all of them into one `PortfolioManager` — a single
shared, risk-managed paper account (leverage cap, portfolio-wide risk cap across open positions, a
drawdown throttle that cuts risk-per-trade after a losing streak, max one open LONG + one open SHORT
at a time regardless of which combo fired). State resumes across runs from `portfolio_state`/
`portfolio_trades` in Mongo (`app/database/portfolio_store.py`), keyed by `entry_time` rather than a
row index — the task only fetches a small rolling window, never full history, so already-open
positions from earlier runs need to be locatable without the bars they were opened on.

`CombinedPortfolioStrategy` (used by pipeline 1) and the Portfolio pipeline (2) both read the same
`STRATEGIES` combos from `features.py`, but disagree on purpose: pipeline 1 ORs all 20 into a single
BUY/SELL/HOLD per symbol per cycle, tracked in one `PaperBroker` account; pipeline 2 lets every combo
trade independently inside one shared risk-managed pot. They are not synchronized and don't share state.

### Strategy auto-discovery

`app/core/settings.get_strategies()` reads the `STRATEGIES` env var: either `"*"` (auto-discover every
concrete `BaseStrategy` subclass under `app/strategies/` via `_discover_strategy_class_paths`, skipping
any module whose filename starts with `_`) or an explicit comma-separated list of dotted class paths.
Under `"*"`, dropping a new file into `app/strategies/` is enough to make it run — no registration step.
To retire a strategy, delete its file (and its `app/strategies/__init__.py` import) rather than trying
to exclude it via the env var, since `"*"` mode ignores any explicit exclusions.

### `PaperBroker` accounts are keyed by the exact `strategy_name` string

`app/core/paper_broker.py`: one virtual $100 account per `(strategy_name, symbol)` pair,
`_id = f"{strategy_name}::{symbol}"`, Redis-locked per account. **If a strategy's `StrategyResult.strategy_name`
varies between cycles** (e.g. appending which sub-condition fired), every distinct string silently
creates a brand-new fragmented account instead of accumulating trade history in one place — this
already happened once with `CombinedPortfolioStrategy` (fixed by making it always return the same
`self.name`, logging the specific sub-combo separately rather than folding it into the name). Keep
`strategy_name` stable across calls for any strategy whose account history should stay in one place.

### `app/utility/features.py`: the combo DSL and where `STRATEGIES` comes from

`STRATEGIES` is a dict of `long_01..10`/`short_01..10`, each `{"combo": "...", "direction": ±1}`.
`combo` is a tiny AND-only condition language parsed by `_condition_mask`/`build_direction_array`:
- `"{col}>median"` / `"{col}<median"` — indicator vs. its own trailing rolling median (`CONDITION_WINDOW`, causal)
- `"{signal}(L)"` / `"(S)"` — `sig_{signal}` column equals +1 / -1
- either may carry a `[-k]` suffix (k = 1..3) — same mask, shifted k bars back

These combos are **not invented in this repo** — they're hand-copied from a sibling, separate project
at `~/Desktop/Development/Jay/Master Backtester` (not part of this git history), whose
`combo_backtester.py` exhaustively searches indicator/price-action-signal combinations against real
candle history and writes every profitable one to `report.json`. `features.py`'s `_add_indicators`/
`_add_price_action` are a deliberately trimmed, hand-synced port of that project's `indicator_engine.py`/
`price_action_engine.py` — they compute only what the *current* `STRATEGIES` entries reference, nothing
more, so adding a combo that needs a new `sig_*`/indicator column means porting that column's formula
over too (`build_direction_array` fails soft — an unresolvable column makes that one combo permanently
all-zero rather than crashing the ensemble, logged once via `_warned_missing_signals`).

Raw top-PnL-by-report.json should not be trusted directly — it's a single-period in-sample search over
hundreds of millions of candidates, which surfaces plenty of noise alongside real edges. The current
set was chosen by re-simulating top candidates on a 70/30 time split and keeping only ones profitable
on *both* halves, ranked by whichever half did worse. Refreshing the combo set should follow the same
pattern, not just copy the new top-N.

### Dashboard is a thin, fully data-driven read layer

`app/dashboard/main.py` (FastAPI) + `app/dashboard/static/` query the same Mongo collections the Celery
side writes to directly (`broker_accounts`, `broker_trades`, `batch_results`, `portfolio_state`,
`portfolio_trades`, `signals_log`, `system_status`) and tail `logs/*.log`. There's no separate API/service
layer and no hardcoded strategy list — whatever shows up in `broker_accounts` shows up on the dashboard.

### Data fetching & caching

`app/utility/data_provider.fetch_historical_data` pulls OHLCV from Delta Exchange's public API
(`api.india.delta.exchange`), Redis-cached (DB 3 — separate from Celery's broker/backend/pubsub DBs
0/1/2) with a 2-minute default TTL, overridable per call (`ttl=`) — strategies that reference longer
timeframes pass a longer TTL since those change rarely. `1M`/`1w` aren't supported natively by the
exchange API — fetched as `1d` and resampled locally.

### Deployment

The GCP VM runs the same `docker-compose.yml`, with `.:/app` bind-mounted into worker/beat/dashboard —
a `git pull` + `docker compose restart <service>` picks up code changes without an image rebuild.
There is no CI/CD in this repo.
