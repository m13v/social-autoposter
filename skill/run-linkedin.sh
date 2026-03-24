#!/bin/bash
# Social Autoposter - LinkedIn posting only
# Finds LinkedIn posts and adds ONE comment per run.
# Called by launchd every 3 hours.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-linkedin-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== LinkedIn Post Run: $(date) ===" | tee "$LOG_FILE"

# Rate limit check: max 5 LinkedIn posts per 24 hours
COUNT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE platform='linkedin' AND posted_at >= NOW() - INTERVAL '24 hours'" 2>/dev/null || echo "0")
if [ "$COUNT" -ge 5 ]; then
  echo "Rate limit reached: $COUNT LinkedIn posts in last 24h (max 5). Skipping." | tee -a "$LOG_FILE"
  exit 0
fi
echo "Rate limit OK: $COUNT LinkedIn posts in last 24h (max 5)" | tee -a "$LOG_FILE"

claude -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for the linkedin_topics list and account name.

Run the **Workflow: Post** section for **LinkedIn ONLY**. Follow every step:
1. Find candidate posts: python3 $REPO_DIR/scripts/find_threads.py --include-linkedin
   From the output, pick ONLY linkedin candidates (discovery_method: search_url).
   Browse the search URL via mcp__linkedin-agent__browser_navigate to find actual posts.
2. Pick the best LinkedIn post to comment on
3. Draft the comment (follow Content Rules - NEVER use em dashes, professional but casual tone)
4. Post it using the linkedin-agent browser (mcp__linkedin-agent__* tools)
5. Log to database

ONE post per run max. If nothing fits, say '## No good post found' and stop.

CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: Use ONLY mcp__linkedin-agent__* tools. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__*.
CRITICAL: If a browser tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). If still blocked, skip and stop." --max-turns 50 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-linkedin-*.log" -mtime +7 -delete 2>/dev/null || true
