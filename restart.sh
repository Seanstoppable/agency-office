#!/usr/bin/env bash
# Restart the Agency Session Dashboard (port 8420)
set -e

PORT=8420
DIR="$(cd "$(dirname "$0")" && pwd)"

# Kill existing process on the port
PID=$(lsof -ti:$PORT 2>/dev/null || true)
if [ -n "$PID" ]; then
    kill "$PID" 2>/dev/null || true
    sleep 1
fi

# Start the server
cd "$DIR"
source .venv/bin/activate
nohup python app.py > /tmp/dashboard.log 2>&1 &
sleep 2

# Health check
if curl -s -o /dev/null -w '' --fail http://127.0.0.1:$PORT/ 2>/dev/null; then
    echo "✓ Dashboard running at http://127.0.0.1:$PORT/"
else
    echo "✗ Failed to start — check /tmp/dashboard.log"
    exit 1
fi
