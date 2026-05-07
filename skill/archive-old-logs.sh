#!/bin/bash
# Archive log files older than 7 days from skill/logs/ to skill/logs-archive/.
# The dashboard (bin/server.js) does many fs.readdirSync(LOG_DIR) calls per
# pulse. Letting that directory grow to 17k+ files starves the event loop
# and the dashboard stops responding. Pruning to a sibling dir keeps the
# files around for forensics without including them in the dashboard scan.
#
# Scheduled daily by ~/Library/LaunchAgents/com.m13v.social-archive-logs.plist

set -uo pipefail

LOG_DIR="/Users/matthewdi/social-autoposter/skill/logs"
ARCHIVE_DIR="/Users/matthewdi/social-autoposter/skill/logs-archive"
DAYS="${ARCHIVE_DAYS:-7}"

mkdir -p "$ARCHIVE_DIR" "$LOG_DIR"

# Per-run summary log so the dashboard's "Other" section can find this job.
# Filename matches the JOBS[].logPrefix value in bin/server.js.
RUN_LOG="$LOG_DIR/archive-logs-$(date +%Y-%m-%d_%H%M%S).log"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$RUN_LOG"; }

if [ ! -d "$LOG_DIR" ]; then
  log "ERROR: LOG_DIR not found: $LOG_DIR"
  exit 0
fi

log "=== archive-old-logs starting (DAYS=$DAYS) ==="

# Only top-level files; do not touch claude-sessions/ or other subdirs.
# Also exclude the per-run summary we just created so we don't archive
# ourselves on long-tail edge cases.
find "$LOG_DIR" -maxdepth 1 -type f -mtime +"$DAYS" ! -name "$(basename "$RUN_LOG")" -print0 \
  | xargs -0 -I{} mv {} "$ARCHIVE_DIR/" 2>&1 | tee -a "$RUN_LOG" >/dev/null || true

remaining=$(find "$LOG_DIR" -maxdepth 1 -type f | wc -l | tr -d ' ')
archived=$(find "$ARCHIVE_DIR" -maxdepth 1 -type f | wc -l | tr -d ' ')

log "kept=$remaining archived_total=$archived"
log "=== done ==="
