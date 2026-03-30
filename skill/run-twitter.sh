#!/bin/bash
# Social Autoposter - Twitter/X posting only
# Finds Twitter threads and posts up to 3 replies per run.
# Called by launchd every 2 hours.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-twitter-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Twitter Post Run: $(date) ===" | tee "$LOG_FILE"

# Generate top performers feedback report (Twitter-specific)
TOP_REPORT=$(python3 "$REPO_DIR/scripts/top_performers.py" --platform twitter 2>/dev/null || echo "(top performers report unavailable)")

claude -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for the twitter_topics list and account handle.

## FEEDBACK FROM PAST PERFORMANCE (use this to write better replies):
$TOP_REPORT

Run the **Workflow: Post** section for **Twitter/X ONLY**. Follow every step:
1. Find candidate tweets: python3 $REPO_DIR/scripts/find_threads.py --include-twitter
   From the output, pick ONLY twitter candidates (discovery_method: search_url).
   Browse the search URL via mcp__twitter-agent__browser_navigate to find actual tweets.
2. Pick the best tweet to reply to
3. Draft the reply (follow Content Rules - NEVER use em dashes, keep it short 1-2 sentences)
4. Post it using the twitter-agent browser (mcp__twitter-agent__* tools)
5. Determine project_name by matching thread topic to config.json projects[].topics
6. Log to database (MUST include project_name AND feedback_report_used=TRUE in the INSERT)

Up to 3 posts per run. If nothing fits, say '## No good tweet found' and stop.

CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: Use ONLY mcp__twitter-agent__* tools. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__*.
CRITICAL: If a browser tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). If still blocked, skip and stop." 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-twitter-*.log" -mtime +7 -delete 2>/dev/null || true
