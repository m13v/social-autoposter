#!/usr/bin/env bash
# promote-engagement-styles.sh — graduate model-invented candidate styles to active
# (or retire underperformers). Reads scripts/engagement_styles_extra.json,
# evaluates each candidate against platform median engagement, writes the
# updated sidecar back atomically.
#
# Runs daily via launchd com.m13v.social-promote-engagement-styles.plist.

set -uo pipefail

# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/promote-engagement-styles-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

if [ -z "${DATABASE_URL:-}" ]; then
    log "ERROR: DATABASE_URL not set"
    exit 1
fi

RUN_START=$(date +%s)
log "=== Engagement style promoter: $(date) ==="

OUTPUT=$(python3 "$REPO_DIR/scripts/promote_engagement_styles.py" 2>&1) || true
echo "$OUTPUT" | tee -a "$LOG_FILE"

PROMOTED=$(echo "$OUTPUT" | grep -c '^  \[promote\]' | head -1)
RETIRED=$(echo "$OUTPUT" | grep -c '^  \[retire\]' | head -1)
LEFT=$(echo "$OUTPUT" | grep -c '^  \[leave\]' | head -1)

log "Summary: promoted=$PROMOTED retired=$RETIRED left=$LEFT"

RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
python3 "$REPO_DIR/scripts/log_run.py" --script "promote-engagement-styles" \
    --posted "$PROMOTED" --skipped "$LEFT" --failed "$RETIRED" \
    --cost 0 --elapsed "$RUN_ELAPSED" 2>/dev/null || true

log "=== Done in ${RUN_ELAPSED}s ==="

find "$LOG_DIR" -name "promote-engagement-styles-*.log" -mtime +30 -delete 2>/dev/null || true
