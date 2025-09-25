#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
cd "$PROJECT_ROOT"

export CELERY_BROKER_URL=${CELERY_BROKER_URL:-amqp://guest:guest@localhost:5672//}
export CELERY_RESULT_BACKEND=${CELERY_RESULT_BACKEND:-redis://localhost:6379/0}
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"

echo "[1/4] Bringing up RabbitMQ, Redis, Flower via docker compose..."
docker compose up -d

echo "[2/4] Installing deps..."
source .venv/bin/activate
pip install -r requirements.txt

echo "[3/4] Starting Celery worker with concurrency=9..."
celery -A core.celery_app.celery_app worker --loglevel=INFO --pool=prefork --concurrency=9 &
WORKER_PID=$!

trap 'echo "Stopping worker ($WORKER_PID)"; kill $WORKER_PID || true' EXIT

sleep 2

echo "[4/4] Running app.py batch..."
python app.py

echo "Done. Results JSON saved in project root."

