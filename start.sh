#!/usr/bin/env bash
# ==============================================================================
# TradeBuddy Pro Development Launcher (Unified Root Script)
# Starts FastAPI Backend (port 8000) & Next.js Frontend (port 3000) with Hot Reload
# ==============================================================================

set -eo pipefail

PROJECT_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$PROJECT_ROOT"

echo "=========================================================="
echo "  🚀 Starting TradeBuddy Pro (Unified Dev Launcher)"
echo "=========================================================="

# Ensure data directory exists
mkdir -p "$PROJECT_ROOT/data"

# Check Python virtualenv
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
if [ ! -f "$VENV_PYTHON" ]; then
    echo "⚙️ Setting up virtualenv (.venv)..."
    if command -v uv &>/dev/null; then
        uv venv -p 3.13 "$PROJECT_ROOT/.venv" || uv venv "$PROJECT_ROOT/.venv"
        uv pip install -p "$VENV_PYTHON" -e "$PROJECT_ROOT/backend[dev]" || uv pip install -e "$PROJECT_ROOT/backend"
    else
        python3 -m venv "$PROJECT_ROOT/.venv"
        "$VENV_PYTHON" -m pip install -e "$PROJECT_ROOT/backend"
    fi
fi

# Ensure frontend dependencies are installed
if [ ! -d "$PROJECT_ROOT/frontend/node_modules" ]; then
    echo "📦 Installing frontend dependencies in ./frontend..."
    (cd "$PROJECT_ROOT/frontend" && npm install)
fi

# Cleanup handler for graceful shutdown on Ctrl+C or kill
BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
    echo ""
    echo "🛑 Stopping TradeBuddy development servers..."
    if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        kill -TERM "$BACKEND_PID" 2>/dev/null || true
    fi
    if [ -n "$FRONTEND_PID" ] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
        kill -TERM "$FRONTEND_PID" 2>/dev/null || true
    fi
    wait 2>/dev/null || true
    echo "✅ Servers stopped cleanly."
}
trap cleanup INT TERM

# 1. Start FastAPI Backend with auto-reload using .venv Python
echo "⚡ Starting Backend API on http://127.0.0.1:8000..."
(
    cd "$PROJECT_ROOT/backend"
    exec "$VENV_PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
) &
BACKEND_PID=$!

# 2. Start Next.js Frontend with dev server
echo "🌐 Starting Next.js Frontend on http://localhost:3000..."
(
    cd "$PROJECT_ROOT/frontend"
    exec npm run dev
) &
FRONTEND_PID=$!

echo "=========================================================="
echo "  ✅ TradeBuddy Pro is running in development mode!"
echo ""
echo "  👉 Web Dashboard:  http://localhost:3000"
echo "  👉 API Swagger UI: http://127.0.0.1:8000/docs"
echo "  👉 WebSocket:      ws://127.0.0.1:8000/ws"
echo ""
echo "  Press Ctrl+C at any time to stop both servers."
echo "=========================================================="

# Wait for background processes
wait
