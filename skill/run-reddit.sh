#!/bin/bash
# Social Autoposter - Reddit posting only
# Finds Reddit threads and posts up to 100 comments per run.
# Called by launchd every 1 hour.

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

# Run the posting orchestrator (bare mode, one thread at a time)
python3 "$REPO_DIR/scripts/post_reddit.py" --limit 100 --timeout 3300 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-reddit-*.log" -mtime +7 -delete 2>/dev/null || true
