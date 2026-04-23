#!/bin/bash
# Social Autoposter - Reddit reply scanner
# Runs scan_reddit_replies.py every 5 min via launchd.
# Inbox-based discovery + engage_reddit.py --limit 5 in one job.
# Skip-if-locked (timeout 0) since runs are frequent and a previous tick may still be engaging.


set -euo pipefail

source "$(dirname "$0")/lock.sh"
acquire_lock "scan-reddit-replies" 0

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-scan-reddit-replies-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Scan Reddit Replies Run: $(date) ===" | tee "$LOG_FILE"
START_TS=$(date +%s)

PYTHONUNBUFFERED=1 python3 "$REPO_DIR/scripts/scan_reddit_replies.py" 2>&1 | tee -a "$LOG_FILE" || true

ELAPSED=$(( $(date +%s) - START_TS ))
# grep -c prints "0" AND exits 1 on zero matches, so `|| echo 0` was
# appending a second "0" and making FOUND multiline, which silently broke
# log_run.py. Use `|| FOUND=0` so the fallback only fires when the file is
# unreadable.
FOUND=$(grep -ci "new repl" "$LOG_FILE" 2>/dev/null) || FOUND=0
python3 "$REPO_DIR/scripts/log_run.py" --script "scan_reddit_replies" --posted "$FOUND" --skipped 0 --failed 0 --cost 0 --elapsed "$ELAPSED" || true

echo "=== Scan Reddit Replies complete: $(date) (elapsed ${ELAPSED}s) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-scan-reddit-replies-*.log" -mtime +7 -delete 2>/dev/null || true
