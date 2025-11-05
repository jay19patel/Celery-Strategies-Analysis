# Stock Analysis System

A comprehensive stock analysis system using Celery for distributed task processing, MongoDB for persistent storage, and Redis for both message queuing and real-time pub/sub notifications.

## Architecture

```
┌─────────────────┐
│  Celery Beat    │ ──► Periodic task scheduling
└─────────────────┘
        │
        ▼
┌─────────────────┐     ┌──────────────┐
│  Celery Worker  │ ──► │    Redis     │ ──► Task Queue (DB 0)
└─────────────────┘     │              │ ──► Results (DB 1)
        │               │              │ ──► Pub/Sub (DB 2)
        ▼               └──────────────┘
┌─────────────────┐             │
│    MongoDB      │ ◄───────────┘
│  - batch_       │     Real-time
│    results      │     Publishing
└─────────────────┘
        │
        ▼
┌─────────────────┐
│  Subscribers    │ ──► Real-time consumers
└─────────────────┘
```

## Features

- **Distributed Task Processing**: Celery workers execute trading strategies in parallel
- **Persistent Storage**: MongoDB stores batch execution data
- **Real-time Updates**: Redis pub/sub publishes results instantly to subscribers
- **Task Queuing**: Redis manages Celery task queue and result backend
- **Monitoring**: Flower provides real-time monitoring of Celery workers
- **Scheduled Execution**: Celery Beat runs batch analysis periodically

## Project Structure

```
stockanalysis/
├── app/                          # Main application code
│   ├── core/                     # Core functionality
│   │   ├── base_strategy.py      # Base strategy class
│   │   ├── celery_app.py         # Celery configuration
│   │   ├── settings.py           # Application settings
│   │   ├── strategy_manager.py   # Strategy management
│   │   └── tasks.py              # Celery tasks
│   ├── database/                 # Database layer
│   │   ├── mongodb.py            # MongoDB operations
│   │   └── redis_publisher.py    # Redis pub/sub
│   ├── models/                   # Data models
│   │   ├── analysis_models.py    # Analysis data models
│   │   └── strategy_models.py    # Strategy data models
│   ├── strategies/               # Trading strategies
│   │   ├── ema_strategy.py       # EMA strategy
│   │   ├── rsi_strategy.py       # RSI strategy
│   │   ├── macd_strategy.py      # MACD strategy
│   │   └── bollinger_bands_strategy.py  # Bollinger Bands
│   └── utility/                  # Utility functions
│       └── data_provider.py      # Data fetching utilities
├── docker-compose.yml            # Docker services configuration
├── Dockerfile                    # Docker image definition
├── pyproject.toml               # Python dependencies
└── README.md                    # This file
```

## Components

### 1. Redis
- **Database 0**: Celery broker (task queue)
- **Database 1**: Celery result backend
- **Database 2**: Pub/Sub for real-time broadcasting

### 2. MongoDB Collections
- **batch_results**: Batch execution summaries
  - Indexed by: created_at

### 3. Celery Tasks
- **execute_strategy_task**: Executes a single strategy for a symbol
  - Publishes to Redis pub/sub channel
- **run_all_batch_task**: Runs all strategies for all symbols
  - Saves batch summary to MongoDB
  - Publishes completion event to Redis pub/sub

### 4. Redis Pub/Sub Channels
- **stockanalysis:batch_complete**: Batch completion notifications

## Installation

### Prerequisites
- Docker and Docker Compose
- Python 3.9+

### Setup

1. Clone the repository:
```bash
git clone <repository-url>
cd stockanalysis
```

2. Create environment file:
```bash
cp .env.example .env
```

3. Update `.env` with your configuration (optional - defaults work for local development)

4. Build and start services:
```bash
docker-compose up --build
```

This will start:
- Redis (port 6379)
- MongoDB (port 27017)
- Celery Worker
- Celery Beat (scheduler)
- Flower (monitoring UI on port 5555)

## Usage

### Monitoring

Access Flower monitoring dashboard:
```
http://localhost:5555
```

### Real-time Subscription

Subscribe to batch completion events (recommended):
```bash
python run_subscriber.py
```

This subscriber:
- Listens **only** to `batch_complete` events
- Displays all strategy results grouped by symbol
- Automatically stores complete batch data in MongoDB
- Shows summary of all symbols and their strategy results

Example output:
```
============================================================
📦 BATCH COMPLETE EVENT RECEIVED
============================================================
Batch ID: 507f1f77bcf86cd799439011
Total Results: 15

Summary:
  total_symbols: 3
  total_strategies: 5
  total_tasks: 15

────────────────────────────────────────────────────────────
STRATEGY RESULTS BY SYMBOL:
────────────────────────────────────────────────────────────

📊 BTC-USD - 5 strategies
  1. EMA Strategy
     Signal: BUY | Confidence: 85.00% | Price: 50000.0
  2. RSI Strategy
     Signal: HOLD | Confidence: 60.00% | Price: 50000.0
  ...

✓ Batch data with all 15 strategy results stored in MongoDB
============================================================
```

### Manual Task Execution

Execute a single strategy:
```python
from app.core.tasks import execute_strategy_task

# Execute EMA strategy for BTC-USD
result = execute_strategy_task.delay(
    "app.strategies.ema_strategy.EMAStrategy",
    "BTC-USD"
)
print(result.get())
```

Execute batch analysis:
```python
from app.core.tasks import run_all_batch_task

# Run all strategies for all symbols
result = run_all_batch_task.delay()
print(result.get())
```

### Querying MongoDB

Access MongoDB data:
```python
from app.database.mongodb import get_latest_batch_results

# Get latest batch executions
batches = get_latest_batch_results(limit=10)
```

## Configuration

All configuration is managed through environment variables in `docker-compose.yml`:

**Redis URLs**:
- `REDIS_BROKER_URL`: Redis URL for Celery broker (default: redis://redis:6379/0)
- `REDIS_RESULT_URL`: Redis URL for Celery results (default: redis://redis:6379/1)
- `REDIS_PUBSUB_URL`: Redis URL for pub/sub (default: redis://redis:6379/2)

**MongoDB Atlas**:
- `MONGODB_URL`: Complete MongoDB Atlas connection string (e.g., mongodb+srv://username:password@cluster.mongodb.net/stockanalysis)

**Application**:
- `SYMBOLS`: Comma-separated list of symbols to analyze
- `STRATEGIES`: Comma-separated list of strategy class paths
- `SCHEDULE_SECONDS`: Batch execution interval (default: 60)

**Redis Pub/Sub**:
- `PUBSUB_CHANNEL_STRATEGY`: Channel for strategy results
- `PUBSUB_CHANNEL_BATCH`: Channel for batch completion

**Celery**:
- `TIMEZONE`: Timezone for Celery (default: UTC)
- `ENABLE_UTC`: Enable UTC timezone (default: true)
- `TASK_IGNORE_RESULT`: Ignore task results (default: false)
- `WORKER_PREFETCH_MULTIPLIER`: Worker prefetch multiplier (default: 1)
- `TASK_ACKS_LATE`: Acknowledge tasks late (default: true)
- `BROKER_CONNECTION_RETRY_ON_STARTUP`: Retry broker connection on startup (default: true)

## Development

### Adding New Strategies

1. Create strategy class in `app/strategies/` directory:
```python
from app.core.base_strategy import BaseStrategy
from app.models.strategy_models import StrategyResult

class MyStrategy(BaseStrategy):
    def execute(self, symbol: str) -> StrategyResult:
        # Your strategy logic here
        return StrategyResult(
            symbol=symbol,
            strategy_name="My Strategy",
            signal="BUY",
            confidence=0.85,
            current_price=50000.0
        )
```

2. Add to `.env` file:
```
STRATEGIES=app.strategies.my_strategy.MyStrategy,...
```

### Database Schema

**batch_results** collection:
```json
{
  "_id": ObjectId,
  "summary": {
    "total_symbols": 3,
    "total_strategies": 5,
    "total_tasks": 15
  },
  "results": [...],
  "created_at": ISODate,
  "total_results": 15
}
```

## Docker Services

- **redis**: Message broker and pub/sub (port 6379)
- **mongodb**: Persistent data storage (port 27017)
- **worker**: Celery worker for task execution
- **beat**: Celery beat for scheduled tasks
- **flower**: Monitoring UI (port 5555)

## Data Persistence

All data is persisted in Docker volumes:
- `redis_data`: Redis data
- `mongodb_data`: MongoDB data files
- `mongodb_config`: MongoDB configuration

To remove all data:
```bash
docker-compose down -v
```

## Troubleshooting

### Check Service Health
```bash
docker-compose ps
```

### View Logs
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f worker
docker-compose logs -f mongodb
```

### Connect to MongoDB
```bash
docker exec -it stockanalysis-mongodb mongosh stockanalysis
```

### Connect to Redis
```bash
docker exec -it stockanalysis-redis redis-cli
```

### Test Pub/Sub
```bash
# Terminal 1 - Subscribe
docker exec -it stockanalysis-redis redis-cli
SELECT 2
SUBSCRIBE stockanalysis:strategy_result

# Terminal 2 - Publish test message
docker exec -it stockanalysis-redis redis-cli
SELECT 2
PUBLISH stockanalysis:strategy_result '{"type":"test","data":"hello"}'
```

## Performance Tuning

### Celery Worker Concurrency
Adjust in `docker-compose.yml`:
```yaml
command: celery -A app.core.celery_app.celery_app worker --concurrency=9
```

### MongoDB Indexes
Additional indexes can be created in `app/database/mongodb.py`:
```python
collection.create_index([("field_name", 1)])
```

### Redis Memory
Configure in `docker-compose.yml`:
```yaml
command: ["redis-server", "--maxmemory", "256mb", "--maxmemory-policy", "allkeys-lru"]
```

## License

MIT

## Support

For issues and questions, please open an issue on GitHub.
