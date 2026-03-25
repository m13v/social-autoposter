#!/bin/bash
# Social Autoposter - Reddit posting only
# Finds Reddit threads and posts up to 3 comments per run.
# Called by launchd every 1 hour.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-reddit-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Reddit Post Run: $(date) ===" | tee "$LOG_FILE"

claude -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.

Run the **Workflow: Post** section for **Reddit ONLY**. Follow every step:
1. Find candidate threads: python3 $REPO_DIR/scripts/find_threads.py (Reddit only, no --include-moltbook)
2. Pick the best Reddit thread from the script output
3. Draft the comment (follow Content Rules - NEVER use em dashes)
4. Post it using the reddit-agent browser (mcp__reddit-agent__* tools)
5. Log to database

Up to 3 posts per run. If nothing fits, say '## No good thread found' and stop.

CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: Close browser tabs after every page visit (browser_tabs action 'close', NOT browser_close).
CRITICAL: Use ONLY mcp__reddit-agent__* tools for Reddit. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__*.
CRITICAL: If a browser tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). If still blocked, skip and stop." --max-turns 50 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-reddit-*.log" -mtime +7 -delete 2>/dev/null || true
