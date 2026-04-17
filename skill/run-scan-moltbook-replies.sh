#!/bin/bash
# Social Autoposter - Moltbook reply scanner
# Runs scan_moltbook_replies.py on its own launchd schedule.
# Pure API; typically finishes in <1min, so uses a short 15min lock wait.


set -euo pipefail

source "$(dirname "$0")/lock.sh"
acquire_lock "scan-moltbook-replies" 900

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-scan-moltbook-replies-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Scan Moltbook Replies Run: $(date) ===" | tee "$LOG_FILE"
START_TS=$(date +%s)

PYTHONUNBUFFERED=1 python3 "$REPO_DIR/scripts/scan_moltbook_replies.py" 2>&1 | tee -a "$LOG_FILE" || true

ELAPSED=$(( $(date +%s) - START_TS ))
FOUND=$(grep -ci "new repl" "$LOG_FILE" 2>/dev/null || echo 0)
python3 "$REPO_DIR/scripts/log_run.py" --script "scan_moltbook_replies" --posted "$FOUND" --skipped 0 --failed 0 --cost 0 --elapsed "$ELAPSED" || true

echo "=== Scan Moltbook Replies complete: $(date) (elapsed ${ELAPSED}s) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-scan-moltbook-replies-*.log" -mtime +7 -delete 2>/dev/null || true
