#!/usr/bin/env bash
# stats.sh — Fetch engagement stats via APIs and update the DB.
# Thin wrapper around scripts/update_stats.py.
# Usage: bash stats.sh [--quiet]
# Called by launchd every 6 hours.

set -euo pipefail

REPO_DIR="$HOME/social-autoposter"
DB="$REPO_DIR/social_posts.db"
LOG_DIR="$REPO_DIR/skill/logs"
QUIET="${1:-}"

# Load secrets (MOLTBOOK_API_KEY, etc.)
# shellcheck source=/dev/null
[ -f "$REPO_DIR/.env" ] && source "$REPO_DIR/.env"

mkdir -p "$LOG_DIR"
LOGFILE="$LOG_DIR/stats-$(date +%Y-%m-%d_%H%M%S).log"

echo "[$(date +%H:%M:%S)] Starting stats update" | tee "$LOGFILE"

# Run the Python stats script
if [ "$QUIET" = "--quiet" ]; then
    python3 "$REPO_DIR/scripts/update_stats.py" --db "$DB" --quiet 2>&1 | tee -a "$LOGFILE"
else
    python3 "$REPO_DIR/scripts/update_stats.py" --db "$DB" 2>&1 | tee -a "$LOGFILE"
fi

echo "[$(date +%H:%M:%S)] Stats update complete" | tee -a "$LOGFILE"

# Sync DB to GitHub for Datasette Lite
cd "$REPO_DIR"
git add social_posts.db
git diff --cached --quiet || git commit -m "stats $(date '+%Y-%m-%d %H:%M')" && git push 2>/dev/null || true

# Sync SQLite → Neon Postgres
bash "$REPO_DIR/syncfield.sh" || true
