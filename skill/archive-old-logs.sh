#!/bin/bash
# Archive log files older than 7 days from skill/logs/ to skill/logs-archive/.
# The dashboard (bin/server.js) does many fs.readdirSync(LOG_DIR) calls per
# pulse. Letting that directory grow to 17k+ files starves the event loop
# and the dashboard stops responding. Pruning to a sibling dir keeps the
# files around for forensics without including them in the dashboard scan.
#
# Scheduled daily by ~/Library/LaunchAgents/com.m13v.social-archive-logs.plist

set -euo pipefail

LOG_DIR="/Users/matthewdi/social-autoposter/skill/logs"
ARCHIVE_DIR="/Users/matthewdi/social-autoposter/skill/logs-archive"
DAYS="${ARCHIVE_DAYS:-7}"

mkdir -p "$ARCHIVE_DIR"

if [ ! -d "$LOG_DIR" ]; then
  echo "[archive-old-logs] LOG_DIR not found: $LOG_DIR" >&2
  exit 0
fi

# Only top-level files; do not touch claude-sessions/ or other subdirs.
moved=$(find "$LOG_DIR" -maxdepth 1 -type f -mtime +"$DAYS" -print0 \
        | xargs -0 -I{} mv {} "$ARCHIVE_DIR/" 2>&1 | wc -l | tr -d ' ')

remaining=$(find "$LOG_DIR" -maxdepth 1 -type f | wc -l | tr -d ' ')
archived=$(find "$ARCHIVE_DIR" -maxdepth 1 -type f | wc -l | tr -d ' ')

echo "[$(date '+%Y-%m-%d %H:%M:%S')] archive-old-logs: kept=$remaining archived_total=$archived"
