#!/bin/bash
# Social Autoposter - Twitter/X posting only
# Finds Twitter threads and posts ONE reply per run.
# Called by launchd every 2 hours.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-twitter-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Twitter Post Run: $(date) ===" | tee "$LOG_FILE"

# Rate limit check: max 8 Twitter posts per 24 hours
COUNT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM posts WHERE platform='twitter' AND posted_at >= NOW() - INTERVAL '24 hours'" 2>/dev/null || echo "0")
if [ "$COUNT" -ge 8 ]; then
  echo "Rate limit reached: $COUNT Twitter posts in last 24h (max 8). Skipping." | tee -a "$LOG_FILE"
  exit 0
fi
echo "Rate limit OK: $COUNT Twitter posts in last 24h (max 8)" | tee -a "$LOG_FILE"

claude -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for the twitter_topics list and account handle.

Run the **Workflow: Post** section for **Twitter/X ONLY**. Follow every step:
1. Find candidate tweets: python3 $REPO_DIR/scripts/find_threads.py --include-twitter
   From the output, pick ONLY twitter candidates (discovery_method: search_url).
   Browse the search URL via mcp__twitter-agent__browser_navigate to find actual tweets.
2. Pick the best tweet to reply to
3. Draft the reply (follow Content Rules - NEVER use em dashes, keep it short 1-2 sentences)
4. Post it using the twitter-agent browser (mcp__twitter-agent__* tools)
5. Log to database

ONE post per run max. If nothing fits, say '## No good tweet found' and stop.

CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: Use ONLY mcp__twitter-agent__* tools. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__*.
CRITICAL: If a browser tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). If still blocked, skip and stop." --max-turns 50 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-twitter-*.log" -mtime +7 -delete 2>/dev/null || true
