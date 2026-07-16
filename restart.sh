#!/usr/bin/env bash
# Restart the Agency Session Dashboard (port 8420)
set -e

PORT=8420
DIR="$(cd "$(dirname "$0")" && pwd)"

# Stop any processes bound to the port. lsof can report several PIDs
# (e.g. orphaned reloads), so kill each one and wait until the port is
# actually free before starting a new instance.
stop_on_port() {
    local signal="$1"
    local pids
    pids=$(lsof -ti:"$PORT" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        # shellcheck disable=SC2086
        kill $signal $pids 2>/dev/null || true
    fi
}

wait_for_free() {
    local waited=0
    while lsof -ti:"$PORT" >/dev/null 2>&1; do
        if [ "$waited" -ge 10 ]; then
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
    return 0
}

stop_on_port ""
if ! wait_for_free; then
    echo "… port $PORT still in use, forcing shutdown"
    stop_on_port "-9"
    if ! wait_for_free; then
        echo "✗ Could not free port $PORT — check: lsof -i:$PORT"
        exit 1
    fi
fi

# Start the server
cd "$DIR"
source .venv/bin/activate
nohup python app.py > /tmp/dashboard.log 2>&1 &

# Health check — poll until it responds (app enriches many sessions on boot).
for _ in $(seq 1 10); do
    if curl -s -o /dev/null --fail --max-time 3 "http://127.0.0.1:$PORT/" 2>/dev/null; then
        echo "✓ Dashboard running at http://127.0.0.1:$PORT/"
        exit 0
    fi
    sleep 1
done

echo "✗ Failed to start — check /tmp/dashboard.log"
exit 1
