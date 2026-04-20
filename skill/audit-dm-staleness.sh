#!/usr/bin/env bash
# audit-dm-staleness.sh — Age out no_response DMs after 14 days of silence
# and downgrade stale not_our_prospect escalations.
#
# Runs daily via launchd com.m13v.social-audit-dm-staleness.plist.

set -uo pipefail

# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/audit-dm-staleness-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

if [ -z "${DATABASE_URL:-}" ]; then
    log "ERROR: DATABASE_URL not set"
    exit 1
fi

RUN_START=$(date +%s)
log "=== DM staleness audit: $(date) ==="

# 1. Ghosted outreach: no_response + active + older than 14 days -> stale.
AGED=$(psql "$DATABASE_URL" -t -A -c "
WITH u AS (
    UPDATE dms SET conversation_status='stale'
    WHERE conversation_status='active'
      AND interest_level='no_response'
      AND discovered_at < NOW() - INTERVAL '14 days'
    RETURNING 1
) SELECT COUNT(*) FROM u;" 2>/dev/null || echo "0")
log "Aged ghosted outreach to stale: $AGED"

# 2. Reverse-pitchers that the reply bot escalated: flip back to active.
#    Next inbound will re-evaluate via classifier and re-escalate if truly needed.
DOWNGRADED=$(psql "$DATABASE_URL" -t -A -c "
WITH u AS (
    UPDATE dms SET conversation_status='active'
    WHERE conversation_status='needs_human'
      AND interest_level='not_our_prospect'
    RETURNING 1
) SELECT COUNT(*) FROM u;" 2>/dev/null || echo "0")
log "Downgraded not_our_prospect escalations: $DOWNGRADED"

RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
python3 "$REPO_DIR/scripts/log_run.py" --script "audit-dm-staleness" \
    --posted "$AGED" --skipped "$DOWNGRADED" --failed 0 --cost 0 --elapsed "$RUN_ELAPSED" 2>/dev/null || true

log "=== Done in ${RUN_ELAPSED}s ==="

find "$LOG_DIR" -name "audit-dm-staleness-*.log" -mtime +30 -delete 2>/dev/null || true
