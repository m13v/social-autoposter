#!/bin/bash
# Social Autoposter - Reddit reply scanner (standalone)
# Runs scan_replies.py hourly on its own schedule (previously Phase A of engage.sh).
# Pulls new replies from Reddit for all active posts and inserts into the replies table.

set -euo pipefail

# Platform lock: wait up to 90min for a previous scan to finish, then skip.
# scan_replies is Reddit-API-bound and can take ~85min at the current filter size.
source "$(dirname "$0")/lock.sh"
acquire_lock "scan-replies" 5400

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-scan-replies-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Scan Replies Run: $(date) ===" | tee "$LOG_FILE"
START_TS=$(date +%s)

PYTHONUNBUFFERED=1 python3 "$REPO_DIR/scripts/scan_replies.py" 2>&1 | tee -a "$LOG_FILE" || true

ELAPSED=$(( $(date +%s) - START_TS ))
FOUND=$(grep -ci "new repl" "$LOG_FILE" 2>/dev/null || echo 0)
python3 "$REPO_DIR/scripts/log_run.py" --script "scan_replies" --posted "$FOUND" --skipped 0 --failed 0 --cost 0 --elapsed "$ELAPSED" || true

echo "=== Scan Replies complete: $(date) (elapsed ${ELAPSED}s) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-scan-replies-*.log" -mtime +7 -delete 2>/dev/null || true
