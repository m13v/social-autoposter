#!/bin/bash
# Social Autoposter - Reddit comment posting via search API + CDP browser
#
# Uses post_reddit.py which:
#   1. Searches Reddit (sort=relevance) for topically relevant threads
#   2. Spawns Claude to pick threads and draft comments
#   3. Posts via CDP browser (reddit_browser.py)
#
# Called by launchd every 30 minutes. Runs 5 sequential iterations, each picking
# a different project so weighted distribution stays balanced within a single run.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-reddit-search-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Reddit Search Post Run: $(date) ===" | tee "$LOG_FILE"

# Serialize with other reddit-agent consumers (run-reddit-threads, stats,
# engage-dm-replies, audit-reddit*). Without this, concurrent pipelines hit
# the MCP hook mid-post and fail CDP navigation with "Reddit browser locked
# by session <uuid>" after exhausting retries.
source "$REPO_DIR/skill/lock.sh"
acquire_lock "reddit-browser" 3600

cd "$REPO_DIR"
python3 scripts/post_reddit.py --iterations 5 --limit 1 2>&1 | tee -a "$LOG_FILE"

echo "=== Done: $(date) ===" | tee -a "$LOG_FILE"
