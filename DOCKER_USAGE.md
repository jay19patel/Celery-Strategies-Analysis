# Docker Setup for Stock Analysis

## Quick Start Commands

```bash
# 1. Start all services (Redis + Celery worker + Client)
docker-compose up -d

# 2. Check services are running
docker-compose ps

# 3. Execute your strategy code
docker exec -it stockanalysis_client python example_celery_usage.py

# 4. Stop all services
docker-compose down
```

## Service Explanation

- **Redis**: Task queue storage (runs on port 6379)
- **Worker**: Processes 20 parallel strategy tasks
- **Client**: For submitting tasks and running your code

## Commands for 10×10 Strategy Execution

```bash
# Submit 100 tasks (10 symbols × 10 strategies)
docker exec -it stockanalysis_client python -c "
from core.strategy_manager import StrategyManager
from strategies.ema_strategy import EMAStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.custom_strategy import CustomStrategy

manager = StrategyManager()
manager.add_strategy(EMAStrategy)
manager.add_strategy(RSIStrategy)
manager.add_strategy(CustomStrategy)

symbols = ['AAPL', 'GOOGL', 'MSFT', 'TSLA', 'AMZN', 'META', 'NFLX', 'NVDA', 'AMD', 'INTC']
task_id = manager.execute_symbols_strategies_celery(symbols)
print(f'Batch submitted: {task_id}')
"
```

## Monitoring

```bash
# View worker logs
docker-compose logs -f worker

# View Redis logs
docker-compose logs -f redis
```