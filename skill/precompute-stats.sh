#!/usr/bin/env bash
# precompute-stats.sh — launchd wrapper for scripts/precompute_dashboard_stats.py.
#
# Fires every 5 minutes from com.m13v.social-precompute-stats.plist. Writes
# funnel_stats_<N>d.json, activity_stats_<H>h.json, style_stats_<H>h.json
# snapshots under skill/cache/ so the dashboard serves instant responses
# instead of cold-starting HogQL on every request.
#
# Keep this wrapper small. All business logic lives in the Python script.

set -uo pipefail

REPO_DIR="$HOME/social-autoposter"

# shellcheck source=/dev/null
[ -f "$REPO_DIR/.env" ] && source "$REPO_DIR/.env"

cd "$REPO_DIR" || exit 2

RUN_START=$(date +%s)
python3 "$REPO_DIR/scripts/precompute_dashboard_stats.py"
EXIT_CODE=$?
RUN_ELAPSED=$(( $(date +%s) - RUN_START ))

echo "[$(date +%H:%M:%S)] === done in ${RUN_ELAPSED}s (exit=${EXIT_CODE}) ==="
exit "$EXIT_CODE"
