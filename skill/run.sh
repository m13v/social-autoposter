#!/bin/bash
# Social Autoposter - hourly find & post
# Thin wrapper: loads SKILL.md, Claude runs the Post workflow.
# Called by launchd every hour.

set -euo pipefail

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/skill/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Social Autoposter Run: $(date) ===" | tee "$LOG_FILE"

timeout 1800 claude -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.

Run the **Workflow: Post** section. Follow every step in order:
1. Rate limit check
2. Find candidate threads (use the helper script: python3 $REPO_DIR/scripts/find_threads.py --include-moltbook)
3. Pick the best thread
4. Read the thread + top comments
5. Draft the comment (follow Content Rules - NEVER use em dashes)
6. Post it via Playwright MCP
7. Log to database
8. Self-reply with a relevant project link (mandatory - see SKILL.md step 8)

ONE post per run max. If nothing fits, say '## No good thread found' and stop.

CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: Close browser tabs after every page visit (browser_tabs action 'close', NOT browser_close)." --max-turns 50 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"

# Clean up old logs (keep last 7 days)
find "$LOG_DIR" -name "*.log" -mtime +7 -delete 2>/dev/null || true
