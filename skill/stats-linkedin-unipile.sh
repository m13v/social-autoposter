#!/usr/bin/env bash
# stats-linkedin-unipile.sh — LinkedIn stats via Unipile API (no browser).
#
# Replaces the Claude-driven browser path for engagement stat refreshes.
# Calls update_linkedin_stats_unipile.py which hits Unipile REST API using
# the connected burner account. No linkedin-agent session needed, no
# per-permalink hops, no Voyager API. Safe to run while main account is in
# post-restriction recovery.
#
# Cadence: every 4h via com.m13v.social-linkedin-stats-unipile.plist
# Coverage: 50 rows/fire × 6 fires/day = 300 posts/day, full 984-row
#           sweep in ~3 days. Stale-first ordering ensures freshness.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
PYTHON=/opt/homebrew/bin/python3

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/stats-linkedin-unipile-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "=== LinkedIn Unipile Stats Run: $(date) ==="

SUMMARY=$("$PYTHON" "$REPO_DIR/scripts/update_linkedin_stats_unipile.py" \
    --limit 50 \
    --sleep 30 \
    2>&1 | tee -a "$LOG_FILE" | tail -1)

log "Summary: $SUMMARY"

# Clean up logs older than 14 days.
find "$LOG_DIR" -name "stats-linkedin-unipile-*.log" -mtime +14 -delete 2>/dev/null || true

log "=== Run complete: $(date) ==="
