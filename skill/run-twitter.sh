#!/bin/bash
# Social Autoposter - Twitter/X posting only
# Finds Twitter threads and posts up to 50 replies per run.
# Called by launchd every 2 hours.

set -euo pipefail

# Platform lock: wait up to 60min for previous run to finish, then skip
source "$(dirname "$0")/lock.sh"
acquire_lock "twitter" 3600

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-twitter-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Twitter Post Run: $(date) ===" | tee "$LOG_FILE"

# Pick project based on weight distribution
PROJECT=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform twitter 2>/dev/null || echo "Fazm")
PROJECT_JSON=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform twitter --json 2>/dev/null || echo "{}")
echo "Selected project: $PROJECT" | tee -a "$LOG_FILE"

# Generate top performers feedback report (Twitter + project-specific)
TOP_REPORT=$(python3 "$REPO_DIR/scripts/top_performers.py" --platform twitter --project "$PROJECT" 2>/dev/null || echo "(top performers report unavailable)")

# Step 1: Find candidate tweets via API (no browser needed)
CANDIDATES=$(python3 "$REPO_DIR/scripts/find_tweets.py" --project "$PROJECT" --max 20 --json-output 2>/dev/null || echo "[]")
echo "Candidates found: $(echo "$CANDIDATES" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0)" | tee -a "$LOG_FILE"

claude -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for account handle.

## TARGET PROJECT FOR THIS RUN: $PROJECT
You MUST reply to tweets relevant to this project.
Project config: $PROJECT_JSON
Use this project's content_angle/voice if it has one, otherwise use the global content_angle.
The project_name for all posts this run MUST be '$PROJECT'.

## FEEDBACK FROM PAST PERFORMANCE (use this to write better replies):
$TOP_REPORT

## CANDIDATE TWEETS (found via API search, already deduped against DB):
$CANDIDATES

Run the **Workflow: Post** section for **Twitter/X ONLY**. Follow every step:
1. From the candidates above, pick the best tweets relevant to $PROJECT to reply to.
   Skip any that are not a good fit (too promotional, off-topic, etc.).
2. DEDUP CHECK (MANDATORY before every post): Before replying to any tweet, check if we already posted on it:
   \`\`\`bash
   source ~/social-autoposter/.env
   psql \"\$DATABASE_URL\" -t -A -c \"SELECT id, LEFT(our_content, 80) FROM posts WHERE thread_url LIKE '%TWEET_STATUS_ID%' LIMIT 1;\"
   \`\`\`
   Replace TWEET_STATUS_ID with the tweet's numeric status ID (from the URL).
   If any row is returned, SKIP that tweet and pick another one. Log: \"Skipped tweet TWEET_URL (already posted, post_id=ID)\".
3. Draft the reply using the project's voice/angle (follow Content Rules - NEVER use em dashes, keep it short 1-2 sentences)
4. Post it using the Twitter API:
   \`\`\`bash
   python3 $REPO_DIR/scripts/twitter_api.py post \"REPLY_TEXT\" --reply-to TWEET_ID
   \`\`\`
5. Log to database with project_name='$PROJECT' (MUST include feedback_report_used=TRUE in the INSERT).
   Use the tweet URL and ID returned by the API post command.

Up to 50 posts per run. If nothing fits, say '## No good tweet found' and stop.

CRITICAL: Ignore the 'Max 40 posts per 24 hours' limit in SKILL.md. The actual daily limit is 4000 posts. Post up to 50 per this run.
CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: Post tweets using python3 $REPO_DIR/scripts/twitter_api.py, NOT browser tools. Browser is NOT needed for posting.
CRITICAL: If the API returns an error, log it and skip to the next candidate." 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-twitter-*.log" -mtime +7 -delete 2>/dev/null || true
