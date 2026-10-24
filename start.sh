#!/usr/bin/env bash
# ==============================================================================
# TradeBuddy Pro Development Launcher (Without Docker)
# Starts FastAPI Backend (port 8000) & Next.js Frontend (port 3000) with Hot Reload
# ==============================================================================

set -e

# Resolve actual project root even if called via symlink or from subdirectories
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
    DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
PROJECT_ROOT="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"

cd "$PROJECT_ROOT"

echo "=========================================================="
echo "  🚀 Starting TradeBuddy Pro (Local Development Mode)"
echo "=========================================================="

# Ensure data directory exists
mkdir -p "$PROJECT_ROOT/data"

# Check Python virtualenv
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
if [ ! -f "$VENV_PYTHON" ]; then
    echo "⚙️ Setting up virtualenv (.venv)..."
    uv venv "$PROJECT_ROOT/.venv"
    uv pip install -e "$PROJECT_ROOT/backend"
fi

# Cleanup handler for graceful shutdown on Ctrl+C
cleanup() {
    echo ""
    echo "🛑 Stopping TradeBuddy development servers..."
    if [ -n "$BACKEND_PID" ]; then
        kill "$BACKEND_PID" 2>/dev/null || true
    fi
    if [ -n "$FRONTEND_PID" ]; then
        kill "$FRONTEND_PID" 2>/dev/null || true
    fi
    echo "✅ Servers stopped cleanly."
    exit 0
}
trap cleanup INT TERM EXIT

# 1. Start FastAPI Backend with auto-reload
echo "⚡ Starting Backend API on http://127.0.0.1:8000..."
cd "$PROJECT_ROOT/backend"
"$VENV_PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# 2. Start Next.js Frontend with dev server
echo "🌐 Starting Next.js Frontend on http://localhost:3000..."
cd "$PROJECT_ROOT/frontend"
npm run dev &
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

# Wait for both processes
wait
