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
│  - strategy_    │     Real-time
│    results      │     Publishing
│  - batch_       │
│    results      │
└─────────────────┘
        │
        ▼
┌─────────────────┐
│  Subscribers    │ ──► Real-time consumers
└─────────────────┘
```

## Features

- **Distributed Task Processing**: Celery workers execute trading strategies in parallel
- **Persistent Storage**: MongoDB stores all strategy results and batch execution data
- **Real-time Updates**: Redis pub/sub publishes results instantly to subscribers
- **Task Queuing**: Redis manages Celery task queue and result backend
- **Monitoring**: Flower provides real-time monitoring of Celery workers
- **Scheduled Execution**: Celery Beat runs batch analysis periodically

## Components

### 1. Redis
- **Database 0**: Celery broker (task queue)
- **Database 1**: Celery result backend
- **Database 2**: Pub/Sub for real-time broadcasting

### 2. MongoDB Collections
- **strategy_results**: Individual strategy execution results
  - Indexed by: symbol, strategy_name, timestamp
- **batch_results**: Batch execution summaries
  - Indexed by: created_at

### 3. Celery Tasks
- **execute_strategy_task**: Executes a single strategy for a symbol
  - Saves result to MongoDB
  - Publishes to Redis pub/sub channel
- **run_all_batch_task**: Runs all strategies for all symbols
  - Saves batch summary to MongoDB
  - Publishes completion event to Redis pub/sub

### 4. Redis Pub/Sub Channels
- **stockanalysis:strategy_result**: Individual strategy results
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
from core.tasks import execute_strategy_task

# Execute EMA strategy for BTC-USD
result = execute_strategy_task.delay(
    "strategies.ema_strategy.EMAStrategy",
    "BTC-USD"
)
print(result.get())
```

Execute batch analysis:
```python
from core.tasks import run_all_batch_task

# Run all strategies for all symbols
result = run_all_batch_task.delay()
print(result.get())
```

### Querying MongoDB

Access MongoDB data:
```python
from database.mongodb import get_latest_strategy_results, get_latest_batch_results

# Get latest 100 strategy results
results = get_latest_strategy_results(limit=100)

# Get results for specific symbol
btc_results = get_latest_strategy_results(symbol="BTC-USD", limit=50)

# Get latest batch executions
batches = get_latest_batch_results(limit=10)
```

## Configuration

### Environment Variables

All configuration is managed through environment variables (see `.env.example`):

**Redis**:
- `REDIS_HOST`: Redis hostname (default: localhost)
- `REDIS_PORT`: Redis port (default: 6379)
- `REDIS_BROKER_DB`: Database for Celery broker (default: 0)
- `REDIS_RESULT_DB`: Database for Celery results (default: 1)
- `REDIS_PUBSUB_DB`: Database for pub/sub (default: 2)

**MongoDB**:
- `MONGODB_HOST`: MongoDB hostname (default: localhost)
- `MONGODB_PORT`: MongoDB port (default: 27017)
- `MONGODB_DATABASE`: Database name (default: stockanalysis)
- `MONGODB_USERNAME`: Optional authentication
- `MONGODB_PASSWORD`: Optional authentication

**Application**:
- `SYMBOLS`: Comma-separated list of symbols to analyze
- `STRATEGIES`: Comma-separated list of strategy class paths
- `SCHEDULE_SECONDS`: Batch execution interval (default: 600)

**Pub/Sub**:
- `PUBSUB_CHANNEL_STRATEGY`: Channel for strategy results
- `PUBSUB_CHANNEL_BATCH`: Channel for batch completion

## Development

### Adding New Strategies

1. Create strategy class in `strategies/` directory:
```python
from core.base_strategy import BaseStrategy
from models.strategy_models import StrategyResult

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
STRATEGIES=strategies.my_strategy.MyStrategy,...
```

### Database Schema

**strategy_results** collection:
```json
{
  "_id": ObjectId,
  "symbol": "BTC-USD",
  "strategy_name": "EMA Strategy",
  "signal": "BUY",
  "confidence": 0.85,
  "current_price": 50000.0,
  "timestamp": "2025-01-15T10:30:00",
  "created_at": ISODate,
  "indicators": {...},
  "metadata": {...}
}
```

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
command: celery -A core.celery_app.celery_app worker --concurrency=9
```

### MongoDB Indexes
Additional indexes can be created in `database/mongodb.py`:
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
