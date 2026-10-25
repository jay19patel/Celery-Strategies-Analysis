#!/usr/bin/env bash
# ==============================================================================
# TradeBuddy Pro - Production Backend Launcher
# Runs ONLY the FastAPI backend in production mode (Single Worker, No Reload)
# ==============================================================================

set -eo pipefail

PROJECT_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$PROJECT_ROOT"

# Ensure data and log directories exist
mkdir -p "$PROJECT_ROOT/data"
mkdir -p "$PROJECT_ROOT/backend/data"

PID_FILE="$PROJECT_ROOT/data/backend.pid"
LOG_FILE="$PROJECT_ROOT/data/backend.log"

# Load environment variables if .env exists
if [ -f "$PROJECT_ROOT/.env" ]; then
    # Export non-comment lines
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.env"
    set +a
fi

# Configuration defaults (can be overridden by environment or .env)
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
LOG_LEVEL="${LOG_LEVEL:-info}"
# Downcase LOG_LEVEL for uvicorn
LOG_LEVEL_LOWER="$(echo "$LOG_LEVEL" | tr '[:upper:]' '[:lower:]')"

# Resolve Python executable
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ Error: Virtual environment not found at $VENV_PYTHON"
    echo "💡 Please create the environment first with:"
    echo "   make install"
    echo "   OR: uv venv -p 3.13 .venv && uv pip install -e 'backend[dev]'"
    exit 1
fi

# Function to check if a process with PID is alive
is_pid_running() {
    local pid="$1"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 0
    else
        return 1
    fi
}

# Function to check if the target port is already in use
is_port_in_use() {
    local port="$1"
    if command -v lsof >/dev/null 2>&1; then
        lsof -iTCP:"$port" -sTCP:LISTEN -n -P >/dev/null 2>&1
        return $?
    elif command -v netstat >/dev/null 2>&1; then
        netstat -tuln 2>/dev/null | grep -q ":$port "
        return $?
    else
        return 1
    fi
}

# Production Uvicorn command arguments
# ARCHITECTURAL NOTE: TradeBuddy's Engine (app/engine.py) manages in-memory trading
# state, the Delta WebSocket feed, and APScheduler background tasks in a single process.
# Running multiple uvicorn workers is strictly forbidden because it would duplicate
# the trading engine, order execution, and scheduler jobs.
UVICORN_CMD=(
    "$VENV_PYTHON" -m uvicorn app.main:app
    --host "$HOST"
    --port "$PORT"
    --workers 1
    --proxy-headers
    --forwarded-allow-ips "*"
    --timeout-graceful-shutdown 15
    --lifespan on
    --log-level "$LOG_LEVEL_LOWER"
)

# ------------------------------------------------------------------------------
# Action: Foreground Run (default, suitable for Docker / systemd / terminal)
# ------------------------------------------------------------------------------
run_foreground() {
    echo "=========================================================="
    echo "  🚀 Starting TradeBuddy Backend (Production Mode)"
    echo "=========================================================="
    echo "  📍 Host:       $HOST"
    echo "  🔌 Port:       $PORT"
    echo "  ⚙️  Workers:    1 (Single Engine Invariant)"
    echo "  📝 Log Level:  $LOG_LEVEL_LOWER"
    echo "  🔄 Reload:     OFF"
    echo "  📁 WorkingDir: $PROJECT_ROOT/backend"
    echo "=========================================================="

    if is_port_in_use "$PORT"; then
        echo "⚠️ Warning: Port $PORT appears to be in use already."
        echo "If another backend is running, stop it first."
    fi

    cleanup_foreground() {
        echo ""
        echo "🛑 Received termination signal. Shutting down gracefully..."
    }
    trap cleanup_foreground INT TERM

    cd "$PROJECT_ROOT/backend"
    exec "${UVICORN_CMD[@]}"
}

# ------------------------------------------------------------------------------
# Action: Background Daemon Start
# ------------------------------------------------------------------------------
start_daemon() {
    if [ -f "$PID_FILE" ]; then
        OLD_PID=$(cat "$PID_FILE" 2>/dev/null || true)
        if is_pid_running "$OLD_PID"; then
            echo "⚠️ Backend is already running (PID: $OLD_PID)."
            echo "👉 Check status with: ./start_backend.sh status"
            echo "👉 Or view logs with: ./start_backend.sh logs"
            exit 0
        else
            echo "🧹 Removing stale PID file..."
            rm -f "$PID_FILE"
        fi
    fi

    if is_port_in_use "$PORT"; then
        echo "❌ Error: Port $PORT is already in use by another process."
        exit 1
    fi

    echo "=========================================================="
    echo "  🚀 Starting TradeBuddy Backend as Daemon"
    echo "=========================================================="
    echo "  📍 Host:       $HOST"
    echo "  🔌 Port:       $PORT"
    echo "  📋 Log File:   $LOG_FILE"
    echo "  🆔 PID File:   $PID_FILE"
    echo "=========================================================="

    (
        cd "$PROJECT_ROOT/backend"
        nohup "${UVICORN_CMD[@]}" >> "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
    )

    sleep 1.5

    NEW_PID=$(cat "$PID_FILE" 2>/dev/null || true)
    if is_pid_running "$NEW_PID"; then
        echo "✅ Backend started successfully (PID: $NEW_PID)"
        echo "   API URL:   http://$HOST:$PORT"
        echo "   Docs URL:  http://$HOST:$PORT/docs"
        echo "   View logs: ./start_backend.sh logs"
    else
        echo "❌ Failed to start backend. Checking last lines of $LOG_FILE:"
        tail -n 20 "$LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi
}

# ------------------------------------------------------------------------------
# Action: Stop Daemon
# ------------------------------------------------------------------------------
stop_daemon() {
    if [ ! -f "$PID_FILE" ]; then
        echo "ℹ️ No PID file found at $PID_FILE. Backend may not be running."
        if is_port_in_use "$PORT"; then
            echo "⚠️ A process is listening on port $PORT. You can find its PID via: lsof -i :$PORT"
        fi
        return 0
    fi

    TARGET_PID=$(cat "$PID_FILE" 2>/dev/null || true)
    if ! is_pid_running "$TARGET_PID"; then
        echo "ℹ️ Process $TARGET_PID is not running. Cleaning up stale PID file."
        rm -f "$PID_FILE"
        return 0
    fi

    echo "🛑 Stopping TradeBuddy Backend (PID: $TARGET_PID)..."
    kill -TERM "$TARGET_PID" 2>/dev/null || true

    # Wait up to 15 seconds for graceful exit
    local timeout=15
    while is_pid_running "$TARGET_PID" && [ "$timeout" -gt 0 ]; do
        sleep 1
        timeout=$((timeout - 1))
    done

    if is_pid_running "$TARGET_PID"; then
        echo "⚠️ Graceful shutdown timed out. Forcing termination (SIGKILL)..."
        kill -KILL "$TARGET_PID" 2>/dev/null || true
        sleep 1
    fi

    rm -f "$PID_FILE"
    echo "✅ Backend stopped cleanly."
}

# ------------------------------------------------------------------------------
# Action: Status
# ------------------------------------------------------------------------------
status_daemon() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE" 2>/dev/null || true)
        if is_pid_running "$PID"; then
            echo "🟢 Backend is RUNNING (PID: $PID, Port: $PORT)"
            echo "   Health check: http://$HOST:$PORT/api/health"
            return 0
        fi
    fi

    if is_port_in_use "$PORT"; then
        echo "🟡 Port $PORT is occupied, but PID file is absent or mismatched."
        return 1
    fi

    echo "⚪ Backend is STOPPED."
    return 3
}

# ------------------------------------------------------------------------------
# Action: Tail Logs
# ------------------------------------------------------------------------------
show_logs() {
    if [ ! -f "$LOG_FILE" ]; then
        echo "ℹ️ Log file not found at $LOG_FILE"
        exit 1
    fi
    echo "📋 Streaming logs from $LOG_FILE (Press Ctrl+C to exit)..."
    tail -n 50 -f "$LOG_FILE"
}

# ------------------------------------------------------------------------------
# Entrypoint Router
# ------------------------------------------------------------------------------
ACTION="${1:-run}"

case "$ACTION" in
    run|foreground)
        run_foreground
        ;;
    start|daemon)
        start_daemon
        ;;
    stop)
        stop_daemon
        ;;
    restart)
        stop_daemon
        sleep 1
        start_daemon
        ;;
    status)
        status_daemon
        ;;
    logs)
        show_logs
        ;;
    help|--help|-h)
        echo "TradeBuddy Backend Production Runner"
        echo ""
        echo "Usage: ./start_backend.sh [command]"
        echo ""
        echo "Commands:"
        echo "  run (default)  Run backend in foreground (for systemd, Docker, or terminal)"
        echo "  start          Start backend in background (daemon mode)"
        echo "  stop           Gracefully stop running background backend"
        echo "  restart        Restart background backend"
        echo "  status         Check if backend is currently running"
        echo "  logs           Tail background log output"
        echo "  help           Show this help message"
        echo ""
        echo "Environment Overrides:"
        echo "  HOST           Bind IP (default: 0.0.0.0)"
        echo "  PORT           Port to listen on (default: 8000)"
        echo "  LOG_LEVEL      Log level: debug, info, warning, error (default: info)"
        ;;
    *)
        echo "❌ Unknown command: $ACTION"
        echo "👉 Run './start_backend.sh help' for usage instructions."
        exit 1
        ;;
esac
