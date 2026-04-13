#!/bin/bash
# Social Autoposter - GitHub Issues posting
# Delegates to scripts/post_github.py (Python orchestrator): Claude only drafts
# JSON decisions, Python handles `gh issue comment` posting and DB logging so
# the exact comment text is stored in our_content and our_url is captured.
# Links are added later by Phase D after a comment earns engagement.
# Called by launchd every 4 hours.

set -euo pipefail

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/github-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== GitHub Issues Run: $(date) ===" | tee "$LOG_FILE"

python3 "$REPO_DIR/scripts/post_github.py" --limit 1 --timeout 3600 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"

# Clean up old logs (keep last 7 days)
find "$LOG_DIR" -name "github-*.log" -mtime +7 -delete 2>/dev/null || true
