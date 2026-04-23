#!/usr/bin/env bash
# engage-moltbook.sh — MoltBook reply engagement loop
# Calls engage_reddit.py --platform moltbook to process pending MoltBook replies.
# Discovery runs separately via run-scan-moltbook-replies.sh.
# Called by launchd every 10 minutes.

set -euo pipefail

source "$(dirname "$0")/lock.sh"
acquire_lock "engage-moltbook" 3600

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/engage-moltbook-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

RUN_START=$(date +%s)
log "=== MoltBook Engage Run: $(date) ==="

python3 "$REPO_DIR/scripts/engage_reddit.py" --platform moltbook 2>&1 | tee -a "$LOG_FILE" || log "WARNING: engage_reddit.py exited non-zero"

RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
log "=== MoltBook Engage complete: $(date) (elapsed ${RUN_ELAPSED}s) ==="

find "$LOG_DIR" -name "engage-moltbook-*.log" -mtime +7 -delete 2>/dev/null || true
