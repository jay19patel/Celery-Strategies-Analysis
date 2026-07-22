#!/bin/bash
set -e

cd "$(dirname "$0")"

# Ensure logs directory and files exist
mkdir -p logs
touch logs/signals.log logs/success.log logs/errors.log logs/performance.log

HOST="127.0.0.1"
PORT=8000
URL="http://$HOST:$PORT"

# Free up the port if a previous dashboard instance is still running there
EXISTING_PID="$(lsof -ti tcp:$PORT || true)"
if [ -n "$EXISTING_PID" ]; then
    echo "Stopping existing dashboard on port $PORT..."
    kill $EXISTING_PID 2>/dev/null || true
    sleep 1
fi

echo "Starting Trading Dashboard server at $URL ..."

uv run uvicorn app.dashboard.main:app --host "$HOST" --port "$PORT" &
SERVER_PID=$!

# Stop the server when this script exits (Ctrl+C, terminal close, etc.)
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT INT TERM

# Wait for the server to come up, then open everything in a single browser tab
for i in $(seq 1 30); do
    if curl -s -o /dev/null "$URL"; then
        break
    fi
    sleep 0.5
done

open "$URL"

echo "Dashboard is live at $URL — strategies, trades, and all logs in one view."
echo "Press Ctrl+C to stop the server."

wait $SERVER_PID
