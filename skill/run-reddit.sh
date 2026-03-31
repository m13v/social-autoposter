#!/bin/bash
# Social Autoposter - Reddit posting only (programmatic)
# Uses Playwright + Claude API directly instead of claude -p.
# Claude only drafts comments (~500 tokens/post vs ~50k).
# Called by launchd every 1 hour.

set -euo pipefail

# Platform lock: wait up to 60min for previous run to finish, then skip
LOCK_FILE="/tmp/social-autoposter-reddit.lock"
exec 200>"$LOCK_FILE"
flock -w 3600 200 || { echo "Previous reddit run still active after 60min, skipping"; exit 0; }

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-reddit-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Reddit Post Run (programmatic): $(date) ===" | tee "$LOG_FILE"

python3 "$REPO_DIR/scripts/post_reddit.py" --max-posts 100 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-reddit-*.log" -mtime +7 -delete 2>/dev/null || true
