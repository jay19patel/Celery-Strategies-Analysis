#!/usr/bin/env bash
# ==============================================================================
# TradeBuddy v4 Unified Development Launcher
# Starts Redis (via Docker if available), FastAPI Backend, Celery Worker, & Next.js Frontend
# ==============================================================================

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "=================================================="
echo "  🚀 Starting TradeBuddy Pro (v4 Full-Stack)"
echo "=================================================="

# Ensure data directory exists
mkdir -p data

# 1. Start Redis container if not already running
if command -v docker &> /dev/null; then
    if ! docker ps --format '{{.Names}}' | grep -q "tradebuddy-redis"; then
        echo "📦 Starting Redis container on port 6379..."
        docker run -d --name tradebuddy-redis -p 127.0.0.1:6379:6379 redis:7-alpine redis-server --save "" --appendonly no 2>/dev/null || true
    else
        echo "✅ Redis container already running."
    fi
fi

# 2. Check virtual environment for backend
VENV_PATH="$PROJECT_ROOT/.venv"
if [ ! -d "$VENV_PATH" ]; then
    echo "⚙️ Creating backend Python virtual environment..."
    uv venv "$VENV_PATH"
    "$VENV_PATH/bin/pip" install -e "$PROJECT_ROOT/backend"
fi

# Cleanup function on Ctrl+C (SIGINT/SIGTERM)
cleanup() {
    echo ""
    echo "🛑 Shutting down TradeBuddy services..."
    if [ -n "$BACKEND_PID" ]; then
        kill "$BACKEND_PID" 2>/dev/null || true
    fi
    if [ -n "$CELERY_PID" ]; then
        kill "$CELERY_PID" 2>/dev/null || true
    fi
    if [ -n "$FRONTEND_PID" ]; then
        kill "$FRONTEND_PID" 2>/dev/null || true
    fi
    echo "✅ All services stopped."
    exit 0
}
trap cleanup INT TERM

# 3. Start FastAPI Backend
echo "⚡ Starting FastAPI backend on http://127.0.0.1:8000..."
cd "$PROJECT_ROOT/backend"
"$VENV_PATH/bin/python" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!
cd "$PROJECT_ROOT"

# 4. Start Celery Worker (Optional Background Queue)
echo "👷 Starting Celery strategy worker..."
cd "$PROJECT_ROOT/backend"
"$VENV_PATH/bin/celery" -A app.infrastructure.celery.worker.celery_app worker --loglevel=warning -Q strategies --concurrency=2 &
CELERY_PID=$!
cd "$PROJECT_ROOT"

# 5. Start Next.js Frontend
echo "🌐 Starting Next.js frontend on http://localhost:3000..."
cd "$PROJECT_ROOT/frontend"
npm run dev &
FRONTEND_PID=$!
cd "$PROJECT_ROOT"

echo "=================================================="
echo "  🎉 TradeBuddy Pro is running!"
echo "  - Frontend: http://localhost:3000"
echo "  - Backend API: http://127.0.0.1:8000/docs"
echo "  - WebSocket: ws://127.0.0.1:8000/ws"
echo "  Press Ctrl+C to stop all services."
echo "=================================================="

# Wait for background processes
wait
