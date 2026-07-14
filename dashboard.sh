#!/bin/bash

# Ensure logs directory and files exist
mkdir -p logs
touch logs/signals.log logs/success.log logs/errors.log logs/performance.log

LOGS_DIR="$(pwd)/logs"

echo "Launching Trading Dashboard in Terminal..."

# Open multiple windows in macOS Terminal (avoids keystroke permissions)
osascript <<EOF
tell application "Terminal"
    activate
    
    -- Window 1: Signals
    do script "cd \"$LOGS_DIR\" && clear && echo '=== ALGO SIGNALS ===' && tail -f signals.log"
    
    -- Window 2: Success / General Logs
    do script "cd \"$LOGS_DIR\" && clear && echo '=== SUCCESS LOGS ===' && tail -f success.log"
    
    -- Window 3: Error Logs
    do script "cd \"$LOGS_DIR\" && clear && echo '=== ERROR LOGS ===' && tail -f errors.log"
    
    -- Window 4: Performance Logs
    do script "cd \"$LOGS_DIR\" && clear && echo '=== FINANCIAL PERFORMANCE & STATISTICS ===' && tail -f performance.log"
    
    -- Window 5: Strategy Portfolio Monitor (Paper Broker)
    do script "cd \"$LOGS_DIR/..\" && clear && uv run python app/scripts/monitor_broker.py"
    
end tell
EOF
