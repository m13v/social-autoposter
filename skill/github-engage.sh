#!/usr/bin/env bash
# github-engage.sh — GitHub Issues engagement loop
# Scan our GitHub issue comments for replies, respond to substantive ones.
# Called by launchd every 6 hours.


set -euo pipefail

# GitHub engage lock: wait up to 60min for previous run to finish, then skip
source "$(dirname "$0")/lock.sh"
acquire_lock "github" 3600

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/github-engage-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

RUN_START=$(date +%s)
log "=== GitHub Engagement Run: $(date) ==="

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

# ═══════════════════════════════════════════════════════
# PHASE A: Scan for replies to our GitHub comments
# ═══════════════════════════════════════════════════════
log "Phase A: Scanning GitHub issues for replies..."
python3 "$REPO_DIR/scripts/scan_github_replies.py" 2>&1 | tee -a "$LOG_FILE"

# ═══════════════════════════════════════════════════════
# PHASE B: Respond to pending GitHub replies
# ═══════════════════════════════════════════════════════
PENDING_COUNT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='github' AND status='pending';")

if [ "$PENDING_COUNT" -eq 0 ]; then
    log "Phase B: No pending GitHub replies. Done!"
    RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
    python3 "$REPO_DIR/scripts/log_run.py" --script "engage_github" --posted 0 --skipped 0 --failed 0 --cost 0 --elapsed "$RUN_ELAPSED"
    find "$LOG_DIR" -name "github-engage-*.log" -mtime +7 -delete 2>/dev/null || true
    exit 0
fi

log "Phase B: $PENDING_COUNT pending GitHub replies to process"

# One-at-a-time thread-aware orchestrator. Each reply gets its own Claude session
# with the full issue thread fetched via gh CLI, so Claude can see our prior
# comments and decide reply-or-skip with a JSON escape hatch. See
# scripts/engage_github.py for the prompt and skip-reason contract.
python3 "$REPO_DIR/scripts/engage_github.py" --timeout 3000 2>&1 | tee -a "$LOG_FILE"

# ═══════════════════════════════════════════════════════
# PHASE C: Summary
# ═══════════════════════════════════════════════════════
# engage_github.py already calls log_run.py itself with per-run counts.
# Here we just print the cumulative status for visibility in the log file.
TOTAL_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='github' AND status='pending';")
TOTAL_REPLIED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='github' AND status='replied';")
TOTAL_SKIPPED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE platform='github' AND status='skipped';")

log "GitHub replies cumulative: pending=$TOTAL_PENDING replied=$TOTAL_REPLIED skipped=$TOTAL_SKIPPED"

log "=== GitHub Engagement complete: $(date) ==="

# Clean up old logs
find "$LOG_DIR" -name "github-engage-*.log" -mtime +7 -delete 2>/dev/null || true
