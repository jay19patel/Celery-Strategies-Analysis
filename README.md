## Stock Analysis - Parallel Strategies with Celery

This project runs multiple strategies in parallel across multiple symbols using Celery with RabbitMQ as broker and Redis as result backend.

### Services (via Docker Compose)
- RabbitMQ (broker) at `amqp://guest:guest@localhost:5672//` (UI: `http://localhost:15672`)
- Redis (result backend) at `redis://localhost:6379/0`
- Flower (Celery UI) at `http://localhost:5555`

### Quick Start
1) Bring up infra, start worker (9 procs), and run batch:
```bash
./scripts/run.sh
```

2) Alternatively, run step-by-step:
```bash
docker compose up -d

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

CELERY_BROKER_URL=amqp://guest:guest@localhost:5672// \
CELERY_RESULT_BACKEND=redis://localhost:6379/0 \
celery -A core.celery_app.celery_app worker --loglevel=INFO --pool=prefork --concurrency=9

# In another terminal
source .venv/bin/activate
python app.py
```

### Configure symbols and strategies
Edit `app.py`:
```python
symbols = ["AAPL", "GOOG", "MSFT"]
strategies = [
    "strategies.ema_strategy.EMAStrategy",
    "strategies.rsi_strategy.RSIStrategy",
    "strategies.custom_strategy.CustomStrategy",
]
```

### Output
- Results are saved as `results_YYYYMMDD_HHMMSS.json` in the project root.
- Each symbol aggregates responses from all strategies.

### Flower UI
- After `docker compose up -d`, open `http://localhost:5555` to see tasks and workers.

### Troubleshooting
- NotRegistered: 'execute_strategy_task'
  - Ensure worker uses `-A core.celery_app.celery_app` and that `core.tasks` is importable.
  - Confirm `core/celery_app.py` includes `include=["core.tasks"]` and imports `core.tasks`.
- Connection issues
  - Ensure Docker is running and ports 5672, 6379, 5555 are free.
  - Check RabbitMQ UI at `http://localhost:15672` for broker health.
- Slow or queued batches
  - Increase worker `--concurrency` to match tasks (N symbols × M strategies).
  - Launch additional workers if needed.


