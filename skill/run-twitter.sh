#!/bin/bash
# Social Autoposter - Twitter/X posting only
# Finds Twitter threads and posts up to 50 replies per run.
# Called by launchd every 2 hours.

set -euo pipefail

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

# Generate engagement style and content rules from shared module
STYLES_BLOCK=$(python3 -c "import sys; sys.path.insert(0,'$REPO_DIR/scripts'); from engagement_styles import get_styles_prompt, get_content_rules, get_anti_patterns; print(get_styles_prompt('twitter', context='posting')); print(); print('## Content rules'); print(get_content_rules('twitter')); print(); print(get_anti_patterns())" 2>/dev/null || echo "(style module unavailable)")

claude -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for account handle.

## TARGET PROJECT FOR THIS RUN: $PROJECT
You MUST find tweets relevant to this project and reply about it.
Project config: $PROJECT_JSON
Use this project's content_angle/voice if it has one, otherwise use the global content_angle.
The project_name for all posts this run MUST be '$PROJECT'.

## FEEDBACK FROM PAST PERFORMANCE (use this to write better replies):
$TOP_REPORT

$STYLES_BLOCK

Run the **Workflow: Post** section for **Twitter/X ONLY**. Follow every step:
1. Find candidate tweets: python3 $REPO_DIR/scripts/find_threads.py --include-twitter --project '$PROJECT'
   From the output, pick ONLY twitter candidates (discovery_method: search_url).
   Browse the search URL via mcp__twitter-agent__browser_navigate to find actual tweets.
2. Pick the best tweet relevant to $PROJECT to reply to. Record the exact permalink URL of the tweet you chose (https://x.com/AUTHOR/status/ID).
3. Draft the reply using the engagement style that best fits the tweet. Keep it short, 1-2 sentences. NEVER use em dashes.
4. Post reply AND capture the reply URL using twitter_reply.js in two browser_run_code calls:
   a. Set params: call mcp__twitter-agent__browser_run_code with code:
      async (page) => { await page.evaluate(() => { sessionStorage.setItem('TWEET_URL', 'PERMALINK_FROM_STEP_2'); sessionStorage.setItem('REPLY_TEXT', 'DRAFT_FROM_STEP_3'); }); return 'params set'; }
      Escape single quotes in the draft with \\' as needed.
   b. Run the script: call mcp__twitter-agent__browser_run_code with filename=$REPO_DIR/scripts/twitter_reply.js
      The script returns JSON: {ok, tweet_url, actual_parent_url, actual_parent_id, actual_parent_screen_name, reply_url, verified}.
      - tweet_url is the URL you navigated to
      - actual_parent_url is the AUTHORITATIVE parent from Twitter's CreateTweet API response (may differ from tweet_url if you navigated to a mid-thread tweet and Twitter re-anchored)
      - reply_url is the URL of the reply you just posted
   c. If ok=false OR reply_url is null OR actual_parent_url is null: the post FAILED or could not be verified. Do NOT log to DB. Skip this tweet and move on.
5. Log to database with project_name='$PROJECT' and engagement_style='STYLE_YOU_CHOSE'. The INSERT MUST use these values verbatim from the twitter_reply.js output:
   - thread_url = actual_parent_url (NOT tweet_url, NOT the candidate URL from find_threads, NOT any URL from your memory)
   - our_url = reply_url
   - feedback_report_used = TRUE
   - engagement_style = the style name you chose (critic, snarky_oneliner, etc.)
   These fields are non-negotiable. Do not substitute values from other sources.

Up to 50 posts per run. If nothing fits, say '## No good tweet found' and stop.

CRITICAL: Ignore the 'Max 40 posts per 24 hours' limit in SKILL.md. The actual daily limit is 4000 posts. Post up to 50 per this run.
CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: Use ONLY mcp__twitter-agent__* tools. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__*.
CRITICAL: For POSTING replies, use ONLY the twitter_reply.js flow described in step 4. NEVER post manually via browser_click/browser_type on the reply UI, and NEVER use scripts/twitter_browser.py. Manual posting cannot capture the authoritative parent URL and has produced mismatches between thread_url and the real reply target.
CRITICAL: If a browser tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). If still blocked, skip and stop." 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-twitter-*.log" -mtime +7 -delete 2>/dev/null || true
