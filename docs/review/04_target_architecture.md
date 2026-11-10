# 04 — Target Architecture Proposal

## 0. Premise correction (important)

The brief describes a "Python backend (Celery-based)" and asks for Redis, Postgres, Celery queues
and distributed locks. **This repository contains no Celery, Redis or Postgres.** The repo name is
historical. The backend is **one FastAPI process** that owns an `Engine` (`backend/app/engine.py`):
an in-process APScheduler runs 17 timed jobs, an in-process `EventHub` fans events out, and SQLite
(WAL) holds all state. The engine is the only writer, and every state change goes through one
`asyncio.Lock` in `TradingService` (`trading/service.py:160`).

For a single-user bot trading two underlyings on one exchange, that design is **correct**, and it
removes a whole class of bugs (two workers acting on one position) that the requested Redis locks
would exist to solve. So this proposal keeps the runtime and fixes what actually blocks the goals:

1. Adding or removing a strategy must be one file.
2. One risk manager with portfolio-level limits and a real kill switch.
3. The exchange's positions reconciled with the database.
4. Fewer settings, generated from strategy schemas.

## 1. Target diagram

```mermaid
flowchart LR
  subgraph Market["Market data (unchanged)"]
    WS[Delta WebSocket] --> Feed[MarketFeed]
    REST[Delta REST] --> Feed
    Feed --> Bars[(SQLite: bars, chains, flow)]
  end

  subgraph Plugins["strategies/ — auto-discovered"]
    P1[trend_4h.py]
    P2[iron_condor.py]
    P3[... one file each]
  end

  Registry[registry.discover()] --> Plugins
  Runner[StrategyRunner<br/>one loop for every family] -->|Context| Plugins
  Plugins -->|Signal| Risk

  subgraph Risk["RiskManager (new, one place)"]
    R1[halt / kill switch / max drawdown]
    R2[portfolio heat + per-underlying cap]
    R3[edge scale x drawdown scale -> size]
  end

  Risk -->|Order intent + qty| Exec[TradingService<br/>execution, idempotent ids]
  Exec --> Brokers{Broker protocol}
  Brokers --> Paper[PaperBroker]
  Brokers --> Delta[DeltaBroker]
  Recon[Reconciler<br/>startup + every 5 min] --> Delta
  Recon --> DB[(SQLite: structures, legs, orders, decisions)]
  Exec --> DB
  Guard[Guard 5s] --> Exec
  Exec --> Hub[EventHub] --> API[FastAPI /api + /ws] --> UI[Next.js]
  Hub --> Notify[Telegram / email]
  Settings[(settings.strategies: per-strategy config)] --> Runner
  Registry -->|Params JSON schema| API
```

Before/after for the summary is in `00_summary.md`.

## 2. Strategy plugin system (core requirement)

### 2.1 What exists today and why it fails the one-file rule

| Family | Files to touch to add one |
|---|---|
| Directional (perp) | new file in `strategies/`; import + instance in `strategies/__init__.py:14`; name in the `Directional` Literal in `settings.py:38`; frontend lists (see `05_ui_review.md`) |
| Option structure | `StrategySpec` in `CATALOG` and builder in `BUILDERS` (`options/strategies.py`); a hand-written score block in `options/regime.py:146-202`; `STRATEGY_KEYS` **and** `Strategy` Literal in `settings.py` (two copies); frontend |
| Zero-hero / decay | hard-wired module pair, its own scheduler job, 19 settings, its own page |

The perp `Strategy` base (`strategies/base.py:98`) is already close to right: pure `decide()`,
declared data needs, never touches the exchange. Keep that and extend it.

### 2.2 Interface

```python
# backend/app/strategies/base.py
class BaseStrategy(ABC):
    name: ClassVar[str]                 # stable key; rows and settings reference it
    version: ClassVar[int] = 1          # bump when decide() changes behaviour
    label: ClassVar[str]
    family: ClassVar[Literal["perp", "options"]]

    class Params(BaseModel):            # defaults + validation; becomes the UI form
        model_config = ConfigDict(extra="forbid")

    @classmethod
    @abstractmethod
    def required_data(cls) -> DataNeeds: ...   # interval, lookback, flow/bias/chain flags

    @abstractmethod
    def generate_signal(self, ctx: Context, p: "BaseStrategy.Params") -> Signal | None: ...
```

- `Signal` is one dataclass with the family's payload: `PerpSignal(side, entry, stop, target, reason)`
  or `OptionSignal(legs picked by strike rule, score, reasons)`. Pricing, checks, sizing and
  execution stay shared, exactly as today.
- An option structure's file holds its spec, its strike builder **and** its regime score, so
  `regime.score()` becomes a loop over the registry instead of a 60-line hand-written block.
- `generate_signal` stays pure (no clock, no network): the same class runs in backtest, paper and live.

### 2.3 Auto-discovery

```python
# backend/app/strategies/registry.py
def discover() -> dict[str, type[BaseStrategy]]:
    found = {}
    for mod in pkgutil.iter_modules(strategies.__path__):
        if mod.name.startswith("_") or mod.name in ("base", "registry"):
            continue
        module = importlib.import_module(f"app.strategies.{mod.name}")
        for obj in vars(module).values():
            if isinstance(obj, type) and issubclass(obj, BaseStrategy) and not inspect.isabstract(obj):
                if obj.name in found:
                    raise RuntimeError(f"duplicate strategy name {obj.name}")
                found[obj.name] = obj
    return found
```

Deleting a file removes the strategy. Stored config for a name that no longer exists is ignored
(the existing `from_stored` already drops unknown values field by field). Historical rows keep
their strategy name and render with a generic label — `label_of` already falls back.

### 2.4 Configuration

Replace `directional_strategies`, `strategy_symbols`, `auto_strategies` and the two Literals with one field:

```python
class StrategyConfig(BaseModel):
    enabled: bool = False                      # new strategies start off
    brokers: list[Literal["paper", "delta"]] = ["paper"]   # paper vs live per strategy
    underlyings: list[Underlying] = []         # empty = all enabled underlyings
    params: dict[str, Any] = {}                # validated by the strategy's Params

strategies: dict[str, StrategyConfig] = {}     # validated against the registry at load
```

`/api/strategies` returns each strategy's `describe()`, its config and `Params.model_json_schema()`.
The frontend renders the form from the schema, so a new strategy needs no frontend code.

**Live mode stays fail-closed:** `brokers` containing `delta` is honoured only if the existing gates
pass (Delta active, `auto_delta` on, and `trading/edge.py` has measured a positive edge on paper).

## 3. Layers

| Layer | Target | Change from today |
|---|---|---|
| Market data | `MarketFeed` | none |
| Event bus | in-process `EventHub` | none — Redis Streams would add a broker for one consumer process |
| Strategy runner | one `StrategyRunner` for both families | merges `runner.py` and the decision part of `autotrader.py` |
| Risk manager | `trading/risk.py` → `RiskManager.approve(broker, signal) -> Sized \| Refused` | consolidates `service.gate`, `gate_directional`, `autotrader._room`, `zerohero._room`, `edge.scale`, `risk_scale` |
| Execution | `TradingService.open/close` | unchanged core; leverage read from exchange for Delta |
| Exchange adapter | `Broker` protocol (`brokers/base.py`) | already one file per broker; add `positions()` and `open_orders()` for reconciliation |
| Positions/PnL | `structure`/`leg` tables + Reconciler | new reconciler |
| API/UI | FastAPI + Next.js | schema-driven strategy forms, kill switch |

### 3.1 Risk manager rules (one place)
1. Kill switch engaged → refuse everything (persisted, manual resume).
2. Broker halted today (daily loss) → refuse.
3. Unresolved structure on the broker → refuse (keep invariant 3).
4. **Max drawdown** from equity peak ≥ `max_drawdown_pct` (default 25 %) → engage kill switch.
5. **Portfolio heat**: open worst-case losses + this one ≤ `max_heat_pct` (default 4 %) of equity.
6. **Per-underlying**: at most one position per underlying across *all* families, and no opposing directions.
7. Size = equity × `risk_per_trade_pct` × edge scale × drawdown scale (existing `edge.py`).

That replaces `max_open_structures`, `max_per_underlying`, `max_lots` (keep only as a fat-finger cap,
not in the UI) and the zero-hero budget settings.

### 3.2 Reconciliation (new)
On start and every 5 minutes, per Delta broker: read exchange positions and open orders, compare
with `HELD` structures and recorded stop orders. Any mismatch → `ReconcileMismatch` error event,
Telegram alert, and that broker refuses new entries until a human resolves it. It never auto-trades
to "fix" a mismatch.

### 3.3 Idempotency (keep)
Structure ids from broker + request id and `client_order_id` from the structure + leg + attempt
(`service.py:93,926`) are already correct. No change.

## 4. Technology: what to add, what not to

| Tool | Recommendation | Reason |
|---|---|---|
| Pydantic v2 + pydantic-settings | **Already used** | keep |
| `structlog` | **No** | Stdlib `logging` with a JSON formatter and `extra={structure_id, decision_id, strategy}` does the job without a dependency; `Decision`/`SignalLog`/`EventLog` rows already explain trades |
| mypy | **Yes**, dev only, `--strict` on `strategies/`, `trading/`, `brokers/` | cheap, catches money-path type errors |
| ruff | already used | keep |
| pre-commit | optional | `make check` already exists |
| pytest, pytest-asyncio | already used (372 tests pass) | keep |
| hypothesis | **Yes**, dev only | property tests on `pricing.analyze` (max loss vs sampled payoff) and sizing |
| freezegun | **No** | the code already takes `now` as a parameter |
| Telegram notifications | already built | keep |
| backtesting | **Own harness, extended** | `backtest/perp.py` already runs the real `decide()`; vectorbt/backtesting.py cannot run these stateful, multi-input strategies without a rewrite |
| ccxt | **No** | one exchange, options-specific endpoints; the native client is 218 lines and tested |
| **Redis** | **No** | no second process to coordinate; adding workers would *create* the double-trading risk |
| **Celery** | **No** | APScheduler in-process covers 17 jobs; Celery adds a broker, workers, serialization and the concurrency bugs this review would then have to catch |
| **Postgres / TimescaleDB** | **No (now)** | 21 MB SQLite; bars bounded by retention; revisit if more than one writer or > 10 GB |
| Docker Compose | **No** | two processes, `start.sh` works; revisit when deploying to a server |
| Prometheus/Grafana | **No** | the System page already shows job runs and API counters; add a `/api/health` uptime check instead |
| Sentry | Optional | errors already land in the event log and Telegram |

## 5. Migration plan

Each step is one PR, tests first, behaviour identical unless listed.

| Step | Work | Verify |
|---|---|---|
| 1 | Tooling: mypy (dev), JSON log formatter carrying ids | `make check` green; mypy clean on money path |
| 2 | `BaseStrategy` + `registry.discover()`; migrate **`trend_4h_short`** only | golden test: identical `Decision` for every bar of the stored year before/after; backtest output byte-identical |
| 3 | Migrate remaining kept perp strategies; delete `REGISTRY` literal, `Directional` Literal | same golden tests per strategy |
| 4 | Delete the 10 "Remove" strategies (approved bug-fix-level behaviour change), their settings, UI and tests | `make check`; settings count drops |
| 5 | Option structures as plugins (spec + builder + score in one file); delete `CATALOG`/`BUILDERS`/`STRATEGY_KEYS` | golden test: same plan and score from `tests/fixtures/btc_chain.json` |
| 6 | `RiskManager`: kill switch, max drawdown, heat cap, per-underlying cap; fail-closed realised-vol check | new tests per rule; **behaviour change** listed |
| 7 | Reconciler + Delta leverage from exchange | stubbed-client tests for each mismatch type; demo account only |
| 8 | Backtester: trailing stop, funding per 8h, stop slippage; options replay over `data/chains/` | parity test: backtest trade == paper trade on a replayed day |
| 9 | UI: schema-driven forms, kill switch, settings cut (`05_ui_review.md`) | Playwright/manual run on paper |
| 10 | `docs/ADDING_A_STRATEGY.md`, `strategies/_template.py`, project skill `.claude/skills/add-strategy/SKILL.md` | a throwaway strategy added and removed with one file each |

Risks: step 2's golden test needs the stored bars — keep a trimmed fixture in `tests/fixtures/`.
Step 4 changes defaults on a running paper account; close or let positions settle first.
