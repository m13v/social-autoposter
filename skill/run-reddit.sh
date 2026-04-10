#!/bin/bash
# Social Autoposter - Reddit posting only
# Finds Reddit threads and posts up to 100 comments per run.
# Called by launchd every 1 hour.


[ -f "$HOME/.social-paused" ] && echo "PAUSED: ~/.social-paused exists, skipping run." && exit 0
set -euo pipefail

# Platform lock: wait up to 60min for previous run to finish, then skip
source "$(dirname "$0")/lock.sh"
acquire_lock "reddit" 3600

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-reddit-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Reddit Post Run: $(date) ===" | tee "$LOG_FILE"

# Run the posting orchestrator in batches of 5 (small batches = reliable output parsing)
# Each batch gets 600s for Claude to search + draft, total run capped at ~3300s
for BATCH in 1 2 3 4 5 6; do
    echo "[run-reddit] Batch $BATCH/6" | tee -a "$LOG_FILE"
    python3 "$REPO_DIR/scripts/post_reddit.py" --limit 5 --timeout 500 2>&1 | tee -a "$LOG_FILE"
    BATCH_POSTED=$(tail -20 "$LOG_FILE" | grep -o 'posted=[0-9]*' | tail -1 | grep -o '[0-9]*')
    if [ "${BATCH_POSTED:-0}" = "0" ]; then
        echo "[run-reddit] Batch $BATCH posted 0, stopping" | tee -a "$LOG_FILE"
        break
    fi
    sleep 5
done

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-reddit-*.log" -mtime +7 -delete 2>/dev/null || true
