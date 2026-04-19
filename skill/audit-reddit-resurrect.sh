#!/usr/bin/env bash
# audit-reddit-resurrect.sh — Weekly check of Reddit posts marked deleted/removed
# in the last 60 days. If a post is now visible again, flips status back to active.

set -uo pipefail

source "$(dirname "$0")/lock.sh"
acquire_lock "reddit-browser" 3600
acquire_lock "audit-reddit-resurrect" 3600

# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/audit-reddit-resurrect-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" >> "$LOG_FILE"; echo "[$(date +%H:%M:%S)] $*"; }

RUN_START=$(date +%s)
log "=== Reddit resurrect audit: $(date) ==="

CANDIDATES=$(psql "$DATABASE_URL" -t -A -c "
    SELECT COUNT(*) FROM posts
    WHERE platform='reddit' AND status IN ('deleted','removed')
      AND posted_at > NOW() - INTERVAL '60 days'
      AND our_url IS NOT NULL;" 2>/dev/null || echo "0")

log "Candidates: $CANDIDATES posts marked deleted/removed in last 60 days"

python3 "$REPO_DIR/scripts/update_stats.py" --reddit-resurrect --resurrect-days 60 >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ "$EXIT_CODE" -ne 0 ]; then
    log "FAILED (exit $EXIT_CODE)"
else
    log "Done"
fi

RESURRECTED=$(grep -c "^RESURRECTED " "$LOG_FILE" 2>/dev/null || echo "0")
log "Resurrected this run: $RESURRECTED"

RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
python3 "$REPO_DIR/scripts/log_run.py" --script "audit-reddit-resurrect" --posted "$RESURRECTED" --skipped 0 --failed "$EXIT_CODE" --cost 0 --elapsed "$RUN_ELAPSED"

log "=== Reddit resurrect audit complete: $(date) ==="

find "$LOG_DIR" -name "audit-reddit-resurrect-*.log" -mtime +30 -delete 2>/dev/null || true
