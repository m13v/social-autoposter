#!/bin/bash
#
# Daily SEO page expiry cron.
#
# Runs expire_pages.py across every project in config.json. Deletes pages
# that have had ZERO clicks in the last 30 days AND are at least 30 days
# old (file-creation age via git log). Source-of-truth deletes only;
# Next.js will then return 404 and the auto-commit agent pushes the
# deletion within ~60s.
#
# Logs to run_monitor.log as script=seo_expire so the dashboard Jobs
# section picks it up. Bounded by --max so a single run can never nuke
# more than DAILY_MAX pages even if config drifts.
#
# Schedule: launchd com.m13v.seo-expire (daily 04:00 local).
# Ad-hoc:   ./cron_seo_expire.sh
#

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$SCRIPT_DIR/logs/expire"
TS=$(date -u +%Y%m%d-%H%M%S)
LOG_FILE="$LOG_DIR/${TS}.log"

# Daily safety cap. Tune lower to be cautious during ramp-up.
DAILY_MAX="${DAILY_MAX:-50}"

mkdir -p "$LOG_DIR"

# Load env so DATABASE_URL / API keys are available.
[ -f "$ROOT_DIR/.env" ] && set -a && . "$ROOT_DIR/.env" && set +a

PYTHON="/opt/homebrew/bin/python3.11"
EXPIRE="$SCRIPT_DIR/expire_pages.py"
LOG_RUN="$ROOT_DIR/scripts/log_run.py"

START_TS=$(date +%s)

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(ts)] $*" | tee -a "$LOG_FILE"; }

log "=== seo-expire $TS starting (DAILY_MAX=$DAILY_MAX) ==="

# Run the actual deleter. --apply means real deletes; --max caps damage.
# Cap each project independently in spirit by relying on the global cap.
"$PYTHON" "$EXPIRE" --apply --max "$DAILY_MAX" >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

ELAPSED=$(( $(date +%s) - START_TS ))

# Parse the summary line for a deleted count. expire_pages.py prints
# "deleted:          N" only when --apply is set and at least one page was
# processed; default to 0 if no match (e.g. no candidates this run).
DELETED=$(grep -E '^deleted: +[0-9]+' "$LOG_FILE" | tail -1 | awk '{print $2}')
DELETED="${DELETED:-0}"

CANDIDATES=$(grep -E '^total candidates: +[0-9]+' "$LOG_FILE" | tail -1 | awk '{print $3}')
CANDIDATES="${CANDIDATES:-0}"

# skipped = candidates we *could* have deleted but didn't (cap or error)
SKIPPED=$(( CANDIDATES - DELETED ))
[ "$SKIPPED" -lt 0 ] && SKIPPED=0

FAILED=0
[ "$EXIT_CODE" -ne 0 ] && FAILED=1

log "=== seo-expire done (deleted=$DELETED candidates=$CANDIDATES exit=$EXIT_CODE elapsed=${ELAPSED}s) ==="

# Wire into the dashboard Jobs table by writing to run_monitor.log.
"$PYTHON" "$LOG_RUN" \
    --script seo_expire \
    --posted "$DELETED" \
    --skipped "$SKIPPED" \
    --failed "$FAILED" \
    --cost 0.0 \
    --elapsed "$ELAPSED" >/dev/null 2>&1 || true

exit "$EXIT_CODE"
