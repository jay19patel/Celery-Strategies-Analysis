# TradeBuddy Engine: System Architecture & Execution Flow

> **Document Version:** 2.0  
> **Target Audience:** Engineering Leads, DevOps, Algorithmic Trading Architects  
> **Last Updated:** 2026-09-21  

---

## 1. Complete Architecture & Data Flow Visuals

TradeBuddy is a high-throughput, low-latency cryptocurrency algorithmic trading and market analysis engine. It decouples high-frequency strategy computation, asynchronous Celery task distribution, dual-account trade execution (Paper Sandbox & Delta Exchange Live Broker), and multi-channel telemetry distribution (WebSocket + ZeroMQ) into an isolated, process-safe architecture.

### 1.1 End-to-End System Flowchart

The following Mermaid diagram maps the end-to-end data pipeline: from client interaction and periodic timer triggers to worker multiprocessing, Redis coordination, and transactional database persistence.

```mermaid
graph TD
    %% Styling Definitions
    classDef clientStyle fill:#1e293b,stroke:#38bdf8,stroke-width:2px,color:#fff;
    classDef apiStyle fill:#0f172a,stroke:#818cf8,stroke-width:2px,color:#fff;
    classDef redisStyle fill:#450a0a,stroke:#f87171,stroke-width:2px,color:#fff;
    classDef workerStyle fill:#022c22,stroke:#34d399,stroke-width:2px,color:#fff;
    classDef dbStyle fill:#172554,stroke:#60a5fa,stroke-width:2px,color:#fff;
    classDef externalStyle fill:#2e1065,stroke:#c084fc,stroke-width:2px,color:#fff;

    %% Client & Presentation Layer
    subgraph CLIENT_LAYER ["1. Presentation & Control Layer"]
        UI["Web Dashboard UI (HTML5 / Vanilla CSS / JS)<br/>http://localhost:8080"]:::clientStyle
        API_GW["FastAPI Core Service (Uvicorn)<br/>frontend/main.py"]:::apiStyle
    end

    %% Periodic Scheduling Layer
    subgraph SCHEDULER_LAYER ["2. Autonomous Orchestration Layer"]
        BEAT["Celery Beat Scheduler<br/>10s Cadence Tick<br/>app/core/celery_app.py"]:::workerStyle
        BATCH_TRIG["run_all_batch_task<br/>(trigger_batch_execution)<br/>Interval Gate Check"]:::workerStyle
        PORT_TRIG["run_portfolio_task<br/>(Backtester Tick: 300s)"]:::workerStyle
    end

    %% Message Broker & Storage
    subgraph REDIS_LAYER ["3. Redis High-Speed Transport (Port 6379)"]
        R_BROKER["Redis DB 0: Celery Broker<br/>Queues: 'celery' (Tasks / Chords)"]:::redisStyle
        R_BACKEND["Redis DB 1: Celery Results<br/>Chord Barrier / State Sync (TTL: 900s)"]:::redisStyle
        R_DATA["Redis DB 2: Cache & Pub/Sub<br/>- MsgPack OHLCV Candles<br/>- Channel: batch_complete<br/>- Channel: strategy_result"]:::redisStyle
    end

    %% Celery Multiprocessing Worker Layer
    subgraph WORKER_POOL ["4. Celery Multiprocess Execution Pool (9 Prefork Workers)"]
        W_SUPER["Worker Supervisor Process (PID 1)<br/>app/core/tasks.py"]:::workerStyle
        W1["ForkPoolWorker-1 (PID 17)"]:::workerStyle
        W2["ForkPoolWorker-2 (PID 18)"]:::workerStyle
        W3["ForkPoolWorker-3 (PID 19)"]:::workerStyle
        W4["ForkPoolWorker-4 (PID 20)"]:::workerStyle
        W5["ForkPoolWorker-5 (PID 21)"]:::workerStyle
        W6["ForkPoolWorker-6 (PID 22)"]:::workerStyle
        W7["ForkPoolWorker-7 (PID 23)"]:::workerStyle
        W8["ForkPoolWorker-8 (PID 24)"]:::workerStyle
        W9["ForkPoolWorker-9 (PID 25)"]:::workerStyle
        CHORD_CB["Chord Callback Barrier<br/>process_batch_results()"]:::workerStyle
    end

    %% Execution & Risk Layer
    subgraph EXEC_LAYER ["5. Dual Execution & Risk Router"]
        EXEC_MGR["ExecutionManager<br/>app/broker/execution_manager.py"]:::apiStyle
        PAPER_BRK["PaperBroker Sandbox<br/>$100 Virtual Margin Balance<br/>Automated TP/SL Exit Sizing"]:::apiStyle
        DELTA_REST["DeltaClient (REST)<br/>Live Broker Sizing / Signer<br/>app/broker/delta/client.py"]:::apiStyle
    end

    %% Storage & Telemetry
    subgraph PERSISTENCE_LAYER ["6. Transactional Storage & Event Bus"]
        SQLITE[("SQLite Database (WAL Mode)<br/>data/stockanalysis.db<br/>- broker_accounts<br/>- broker_trades<br/>- live_positions<br/>- signals_log")]:::dbStyle
        ZMQ["ZeroMQ EventBus<br/>PUB/SUB Socket on Port 5557<br/>app/core/event_bus.py"]:::externalStyle
    end

    %% External Exchanges
    subgraph EXTERNAL_EXCHANGE ["7. Delta Exchange India API"]
        DELTA_API["REST: api.india.delta.exchange<br/>Candles, Orders, Positions"]:::externalStyle
        DELTA_WS["WSS: socket.india.delta.exchange<br/>Live Order/Position Stream"]:::externalStyle
    end

    %% Flow Relationships
    UI -->|REST: Trigger Batch / Toggle / Settings| API_GW
    API_GW -->|POST /api/batch/run (force=True)| R_BROKER
    API_GW -->|Live /ws/live stream| UI

    BEAT -->|Every 10s Heartbeat| BATCH_TRIG
    BEAT -->|Every 300s| PORT_TRIG
    BATCH_TRIG -->|If Interval Elapsed| R_BROKER
    PORT_TRIG --> R_BROKER

    R_BROKER -->|Fair Dispatch (Prefetch=1)| W_SUPER
    W_SUPER -->|Pre-cache Candles (MsgPack)| R_DATA
    R_DATA <-->|Cache Miss: Fetch OHLCV| DELTA_API

    W_SUPER -->|Parallel Chords Matrix| W1 & W2 & W3 & W4 & W5 & W6 & W7 & W8 & W9
    W1 & W2 & W3 & W4 & W5 & W6 & W7 & W8 & W9 -->|Synchronize Status| R_BACKEND
    R_BACKEND -->|All Tasks Complete| CHORD_CB

    CHORD_CB -->|Actionable BUY / SELL| EXEC_MGR
    CHORD_CB -->|Broadcast Result Payload| R_DATA

    EXEC_MGR -->|If Paper Enabled| PAPER_BRK
    EXEC_MGR -->|If Real Enabled & Armed| DELTA_REST
    DELTA_REST -->|Signed HMAC Orders| DELTA_API

    PAPER_BRK -->|Atomic ACID Write| SQLITE
    EXEC_MGR -->|Log Signal Audit| SQLITE
    DELTA_WS -->|Live Fills / Margins| EXEC_MGR

    API_GW -->|Query Real-time Metrics| SQLITE
    API_GW -->|Collect Worker Telemetry| R_BROKER
    W_SUPER -.->|Broadcast Health Packet| ZMQ
```

---

### 1.2 Step-by-Step Task Execution Lifecycle

Below is the execution walkthrough of a single automated trading cycle:

1. **Autonomous Tick Trigger:**
   - Celery Beat (`app/core/celery_app.py`) fires a `run-batch-periodically` event every 10 seconds.
   - Task `run_all_batch_task` in `app/core/tasks.py` checks whether the dynamic user-configured interval (`SCHEDULE_SECONDS`, default 60s) has elapsed via SQLite's `system_status` table. If 60s have not passed, it exits cleanly in sub-millisecond time.
2. **Step 1.1: Market Data Pre-Caching:**
   - Once the interval elapses, the supervisor task reads configured symbols (e.g. `BTC-USD`, `ETH-USD`, `SOL-USD`) from `system_config`.
   - It fetches the required OHLCV candle windows via `fetch_historical_data()`.
   - Data is stored in **Redis DB 2** using **MsgPack** binary serialization (`delta:candles:{symbol}:{resolution}:{start}:{end}`). Any subsequent task execution within the TTL window experiences a zero-network-latency `Cache HIT`.
3. **Step 1.2: Signature Matrix Generation:**
   - `StrategyManager` computes the Cartesian product of configured **Symbols × Active Strategies**:
     $$\text{Tasks} = N_{\text{symbols}} \times M_{\text{strategies}} = 3 \times 2 = 6 \text{ Tasks}$$
   - Celery `canvas.chord` generates task signatures (`execute_strategy_task.s(...)`) with sequential tracking IDs and routes them into the Redis `celery` queue in **Redis DB 0**.
4. **Step 2: Multiprocess Parallel Execution:**
   - The Celery worker daemon maintains **9 concurrent prefork child worker processes** (`ForkPoolWorker-1` through `ForkPoolWorker-9`).
   - With `worker_prefetch_multiplier=1` and `task_acks_late=True`, workers pick tasks fairly. All 6 tasks execute simultaneously on separate OS processes without Python Global Interpreter Lock (GIL) contention.
   - Technical indicators (EMAs, Supertrend, Bollinger Bands, ATR, RSI) are computed using `pandas-ta` and `scipy`.
5. **Step 3: Chord Barrier Synchronization:**
   - Each worker reports completion into **Redis DB 1** (Result Backend).
   - Once all 6 tasks report completion, Celery's chord barrier unlocks and fires the callback: `process_batch_results()`.
6. **Step 3.1: Protective Risk & Stop-Loss Audit:**
   - Before evaluating new entries, `PaperBroker.check_protective_exit()` inspects every currently open position against fresh mark prices.
   - If a position hits its Stop-Loss or Take-Profit boundary, it is closed immediately with full slippage and fee accounting.
7. **Step 3.2: Execution Manager Routing:**
   - Actionable signals (`BUY` or `SELL`) are routed through `ExecutionManager.process_signal()`:
     - **Paper Enabled:** Routed to `PaperBroker` to update virtual margin balance ($100), leverage (20x), and record position entry in `broker_accounts`.
     - **Real Enabled:** If `LIVE_TRADING_ARMED=True` and API keys exist, routed to `DeltaClient` for signed HMAC-SHA256 order placement on Delta Exchange.
     - **Both Disabled (`LOG_ONLY`):** Recorded strictly as an audit entry in `signals_log` with `action: "logged_only_both_disabled"` without capital risk.
8. **Step 3.3: Telemetry & Persistence:**
   - Batch summary is broadcasted to Redis Pub/Sub channels `stockanalysis:batch_complete` and `stockanalysis:strategy_result`.
   - Results are committed transactionally to `stockanalysis.db` (SQLite in WAL mode).

---

## 2. Live Monitoring & Real-Time System Visualization

### 2.1 Integrated In-Dashboard Telemetry (`http://localhost:8080/#monitoring`)

TradeBuddy incorporates a native, zero-overhead telemetry dashboard that eliminates the need for third-party monitoring daemons:

| Component | Telemetry Rendered | Source Endpoint |
|---|---|---|
| **Celery Worker Pool** | Node name (`celery@...`), Pool type (`PREFORK`), Concurrency (`9 Workers`), RSS Memory (MB), Uptime, Queue Backlog. | `GET /api/system/celery` |
| **Child Process Grid** | Interactive green status chips for all 9 OS process IDs (`PID 17` ... `PID 25`). | `GET /api/system/celery` |
| **Task Breakdown** | Lifetime execution counters for `run_all_batch_task`, `execute_strategy_task`, `process_batch_results`, `run_portfolio_task`. | `GET /api/system/celery` |
| **Delta WebSocket** | Connection status (`LIVE CONNECTED` / `DISCONNECTED`), URL, message count, reconnects, heartbeat. | `GET /api/system/telemetry` |
| **ZeroMQ EventBus** | Port (`5557`), broadcast mode, packet emission counters, active pub topics. | `GET /api/system/telemetry` |
| **Error Log Audit** | Structured table with reverse-chronological sorting, error level badges, collapsible tracebacks, and search pagination. | `GET /api/logs/errors/parsed` |

---

### 2.2 On-Demand Flower Setup (When Deep Celery Inspection is Desired)

Standalone Flower (port 5555) is decommissioned by default to conserve ~120 MB RAM. If deep inspection of Celery internal task state graphs or remote worker revocation is required, launch Flower on-demand:

#### Option A: Run via Docker (Temporary Container)
```bash
# Launch on-demand Flower attached to existing Docker network
docker run --rm -d \
  --name stockanalysis-flower-temp \
  --network celery-strategies-analysis_default \
  -p 5555:5555 \
  mher/flower:latest \
  celery flower --broker=redis://redis:6379/0
```
*Access UI at `http://localhost:5555`. Stop when done with `docker stop stockanalysis-flower-temp`.*

#### Option B: Run Locally via Python / uv
```bash
uv run celery -A app.core.celery_app.celery_app flower \
  --port=5555 \
  --broker=redis://localhost:6379/0
```

---

### 2.3 Production-Safe Redis Monitoring

> [!CAUTION]
> **NEVER run `redis-cli monitor` in production!**  
> `MONITOR` streams every single command processed by Redis to stdout. In a high-frequency system, this can degrade Redis throughput by over **50%**, rapidly spike CPU to 100%, and cause socket connection drops.

#### Recommended Low-Overhead Redis Commands:

```bash
# 1. Check current Celery task queue backlog (instant O(1) complexity)
docker compose exec redis redis-cli -n 0 llen celery

# 2. Check Redis memory usage and fragmentation ratio
docker compose exec redis redis-cli info memory | grep -E "used_memory_human|used_memory_peak_human|mem_fragmentation_ratio"

# 3. Inspect number of active connected clients
docker compose exec redis redis-cli info clients

# 4. Safely scan for largest keys without blocking the event loop
docker compose exec redis redis-cli -n 2 --bigkeys

# 5. Monitor real-time Redis Pub/Sub activity on trading channels
docker compose exec redis redis-cli -n 2 psubscribe "stockanalysis:*"
```

---

### 2.4 Structured Logging & Task Tracing

To trace a specific task across Celery workers, Redis queues, and logs:
- Every Celery task receives a unique UUID `task_id` (`self.request.id`).
- Batches receive a hex-encoded `batch_id` (e.g. `6a85e434035d3b8ebd7f3e0a`).
- All log lines adhere to a standardized delimiter-separated format:
  ```text
  TIMESTAMP | SOURCE_FILE:LINE | FUNCTION | LEVEL | MESSAGE
  ```
- Example log output:
  ```text
  2026-09-21 21:03:29 | tasks.py:82 | execute_strategy_task() | INFO | ✅ STEP 2.1/6 COMPLETED | BTC-USD | CombinedPortfolioStrategy | Signal: HOLD | Time: 0.21s
  ```

---

## 3. Load & Cost Optimization Audit

An architecture review of the active codebase yields the following performance analysis:

### 3.1 Redis Memory & Result Backend Expiry
- **Current Configuration:**
  - `task_ignore_result = True` in [celery_app.py](file:///Users/jaypatel/Desktop/Development/Jay/Celery-Strategies-Analysis/app/core/celery_app.py#L20).
  - `result_expires = 900` (15 minutes).
- **Architectural Verdict (Passed):**
  - Marking `task_ignore_result = True` is a **critical optimization**. It ensures individual tasks (`execute_strategy_task`) do not write task results to Redis DB 1.
  - Redis DB 1 is used **exclusively** by the Celery chord barrier to coordinate parallel completion before firing `process_batch_results`. These coordination keys expire after 15 minutes, preventing Redis memory leakage.
- **Production Recommendation:**
  - Add an explicit memory cap in your Redis startup command or `redis.conf`:
    ```ini
    maxmemory 512mb
    maxmemory-policy volatile-lru
    ```
  - This ensures that if historical data caches in DB 2 grow, Redis automatically evicts the oldest expired keys without crashing.

---

### 3.2 Worker Concurrency & Prefetch Tuning
- **Current Configuration:**
  - Pool: `--pool=prefork` with `--concurrency=9`.
  - Settings: `worker_prefetch_multiplier = 1`, `task_acks_late = True`.
- **Architectural Verdict (Passed):**
  - **Prefork vs Async (Gevent/Eventlet):** Because crypto strategy computation involves intensive statistical calculations (`pandas`, `numpy`, `scipy`, `pandas-ta`), Python threads cannot run in true parallel due to the GIL. The `prefork` pool utilizes separate OS processes across CPU cores, which is the mathematically correct choice for CPU-bound technical indicator computations.
  - **Prefetch Multiplier = 1:** Setting this to `1` ensures **fair distribution**. If Worker 1 is computing a heavy 1-year historical backtest, it will not pre-fetch and hold lighter 15-minute strategy tasks. Idle workers immediately consume pending tasks from Redis.
  - **Acks Late = True:** If an OS container runs out of memory or a worker process is terminated, unacknowledged tasks are re-queued rather than lost.

---

### 3.3 Duplicate Execution & Idempotency
- **Batch Idempotency:**
  - `trigger_batch_execution` verifies `(now_utc - last_triggered_at).total_seconds() < interval` via the database. If multiple timer triggers fire concurrently, duplicate batch executions are discarded early.
- **Database Concurrency & ACID Safety:**
  - `SQLiteDatabase` ([sqlite_db.py](file:///Users/jaypatel/Desktop/Development/Jay/Celery-Strategies-Analysis/app/database/sqlite_db.py)) uses `PRAGMA journal_mode = TRUNCATE` / `WAL` with `PRAGMA busy_timeout = 15000` and thread re-entrant locks (`threading.RLock()`).
  - Table mutations utilize explicit `BEGIN IMMEDIATE;` transactions and SQLite `ON CONFLICT(...) DO UPDATE` upserts for positions and accounts. This prevents race conditions between simultaneous signal updates.

---

### 3.4 Connection Pooling & Fork Safety
- **Redis Publisher Fork Safety:**
  - [redis_publisher.py](file:///Users/jaypatel/Desktop/Development/Jay/Celery-Strategies-Analysis/app/database/redis_publisher.py) inspects `os.getpid()`. When a worker process forks from the main Celery supervisor, it detects the new PID, closes any inherited socket descriptor, and connects freshly. This eliminates socket corruption.
- **SQLite Process Isolation:**
  - `SQLiteDatabase` utilizes `threading.local()` connection caching with `check_same_thread=False` and per-path schema tracking (`_initialized_paths`), supporting seamless concurrent execution in multithreaded and multiprocess environments.

---

## 4. Key Configuration Matrix

| Variable | Current Setting | Purpose | Location |
|---|---|---|---|
| `REDIS_BROKER_URL` | `redis://redis:6379/0` | Celery queue broker | [docker-compose.yml](file:///Users/jaypatel/Desktop/Development/Jay/Celery-Strategies-Analysis/docker-compose.yml#L11) |
| `REDIS_RESULT_URL` | `redis://redis:6379/1` | Chord barrier synchronization | [docker-compose.yml](file:///Users/jaypatel/Desktop/Development/Jay/Celery-Strategies-Analysis/docker-compose.yml#L12) |
| `REDIS_PUBSUB_URL` | `redis://redis:6379/2` | OHLCV cache & event pub/sub | [docker-compose.yml](file:///Users/jaypatel/Desktop/Development/Jay/Celery-Strategies-Analysis/docker-compose.yml#L13) |
| `SQLITE_DB_PATH` | `/app/data/stockanalysis.db` | Embedded ACID storage | [docker-compose.yml](file:///Users/jaypatel/Desktop/Development/Jay/Celery-Strategies-Analysis/docker-compose.yml#L16) |
| `worker --concurrency` | `9` | Parallel OS worker processes | [docker-compose.yml](file:///Users/jaypatel/Desktop/Development/Jay/Celery-Strategies-Analysis/docker-compose.yml#L77) |
| `worker_prefetch_multiplier` | `1` | Fair task scheduling | [celery_app.py](file:///Users/jaypatel/Desktop/Development/Jay/Celery-Strategies-Analysis/app/core/celery_app.py#L21) |
| `task_ignore_result` | `True` | Prevent task result memory bloat | [celery_app.py](file:///Users/jaypatel/Desktop/Development/Jay/Celery-Strategies-Analysis/app/core/celery_app.py#L20) |
| `result_expires` | `900s` | Auto-expire chord barrier keys | [celery_app.py](file:///Users/jaypatel/Desktop/Development/Jay/Celery-Strategies-Analysis/app/core/celery_app.py#L24) |
| `SCHEDULE_SECONDS` | `60s` (Configurable) | Celery Beat batch cycle | [system_service.py](file:///Users/jaypatel/Desktop/Development/Jay/Celery-Strategies-Analysis/app/services/system_service.py#L143) |
| `ZMQ_TELEMETRY_PORT` | `5557` | Non-blocking telemetry PUB/SUB | [event_bus.py](file:///Users/jaypatel/Desktop/Development/Jay/Celery-Strategies-Analysis/app/core/event_bus.py#L19) |
