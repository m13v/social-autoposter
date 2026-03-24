#!/bin/bash
# Social Autoposter - hourly find & post
# Thin wrapper: loads SKILL.md, Claude runs the Post workflow.
# Called by launchd every hour.

set -euo pipefail

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Social Autoposter Run: $(date) ===" | tee "$LOG_FILE"

claude -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.

Run the **Workflow: Post** section. Follow every step in order:
1. Find candidate threads (use the helper script: python3 $REPO_DIR/scripts/find_threads.py --include-moltbook --include-twitter --include-linkedin --force). For Twitter/LinkedIn candidates (discovery_method: search_url), browse the search URL via the platform's dedicated agent to find actual threads.
2. Pick the best thread from the script output
3. Draft the comment (follow Content Rules - NEVER use em dashes)
4. Post it using the correct platform-specific browser agent
5. Log to database

ONE post per run max. If nothing fits, say '## No good thread found' and stop.

CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: Close browser tabs after every page visit (browser_tabs action 'close', NOT browser_close).
CRITICAL: Use the correct browser agent for each platform — Reddit: mcp__reddit-agent__* tools, Twitter: mcp__twitter-agent__* tools, LinkedIn: mcp__linkedin-agent__* tools. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* for platform actions. Each agent has its own browser lock to prevent concurrent session conflicts.
CRITICAL: If a browser agent tool call is blocked or times out, DO NOT fall back to any other browser tool. Wait 30 seconds and retry the same agent. Repeat up to 3 times. If still blocked, skip that platform and move on." --max-turns 50 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"

# Clean up old logs (keep last 7 days)
find "$LOG_DIR" -name "*.log" -mtime +7 -delete 2>/dev/null || true
