#!/bin/bash
# Social Autoposter - GitHub Issues phased posting cycle.
#
# Delegates to scripts/run_github_cycle.py which implements:
#   - Phase 1: gh search across project topics, snapshot T0 comment + reaction counts
#   - Sleep 600s (T0 -> T1 momentum window)
#   - Phase 2a: re-fetch same issues, compute delta_score
#   - Phase 2b: adaptive cap (default 1, bump to 3 when >=3 candidates show momentum),
#              Claude drafts, Python posts via `gh issue comment`
#
# Three reduction levers baked in:
#   (1) Historical (project, style) engagement block in drafter prompt.
#   (2) Adaptive cap gated by per-cycle momentum.
#   (3) T0 -> T1 delta filter: stale issues drop out before Claude sees them.
#
# Called by launchd. Cadence is owned by the .plist, not this script.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/github-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== GitHub Issues Run: $(date) ===" | tee "$LOG_FILE"

python3 "$REPO_DIR/scripts/run_github_cycle.py" 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"

find "$LOG_DIR" -name "github-*.log" -mtime +7 -delete 2>/dev/null || true
