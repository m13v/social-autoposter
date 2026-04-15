#!/bin/bash
# Social Autoposter - Twitter/X posting
# Reads top-scored candidates from twitter_candidates table,
# posts 2-3 replies per run. Called by launchd every 10 minutes.

set -euo pipefail

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-twitter-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Twitter Post Run: $(date) ===" | tee "$LOG_FILE"

# Fetch top candidates from DB
CANDIDATES=$(psql "$DATABASE_URL" -t -A -F '|' -c "
    SELECT id, tweet_url, author_handle, tweet_text, virality_score, matched_project,
           likes, retweets, replies, views, author_followers,
           EXTRACT(EPOCH FROM (NOW() - tweet_posted_at))/3600 AS age_hours
    FROM twitter_candidates
    WHERE status = 'pending' AND virality_score > 5
    ORDER BY virality_score DESC
    LIMIT 3;
" 2>/dev/null || echo "")

if [ -z "$CANDIDATES" ]; then
    echo "No candidates above threshold. Skipping." | tee -a "$LOG_FILE"
    exit 0
fi

# Format candidates for the prompt
CANDIDATE_COUNT=$(echo "$CANDIDATES" | wc -l | tr -d ' ')
echo "Found $CANDIDATE_COUNT candidates" | tee -a "$LOG_FILE"

CANDIDATE_BLOCK=""
while IFS='|' read -r cid curl cauthor ctext cscore cproject clikes crts creplies cviews cfollowers cage; do
    CANDIDATE_BLOCK="${CANDIDATE_BLOCK}
---
Candidate ID: $cid
URL: $curl
Author: @$cauthor (${cfollowers} followers)
Text: $ctext
Score: $cscore | Likes: $clikes | RTs: $crts | Replies: $creplies | Views: $cviews | Age: ${cage}h
Project match: $cproject
"
done <<< "$CANDIDATES"

# Load all project configs for per-candidate routing
ALL_PROJECTS_JSON=$(python3 -c "
import json, os
config = json.load(open(os.path.expanduser('~/social-autoposter/config.json')))
print(json.dumps({p['name']: p for p in config.get('projects', [])}, indent=2))
" 2>/dev/null || echo "{}")

# Generate top performers feedback (platform-wide, not project-specific)
TOP_REPORT=$(python3 "$REPO_DIR/scripts/top_performers.py" --platform twitter 2>/dev/null || echo "(top performers report unavailable)")

# Generate engagement styles
source "$REPO_DIR/skill/styles.sh"
STYLES_BLOCK=$(generate_styles_block twitter posting)

claude -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for account handle.

## PRE-SCORED CANDIDATES (reply to these, best first)
These threads were discovered and scored by the scanner. Reply to the top 2-3.
$CANDIDATE_BLOCK

## PROJECT ROUTING (per-candidate, not per-run)
Each candidate has a 'Project match' field. Use that project for each reply.
If a candidate's project match is empty, pick the most relevant project from the config based on the tweet's topic.
All project configs: $ALL_PROJECTS_JSON

## FEEDBACK FROM PAST PERFORMANCE:
$TOP_REPORT

$STYLES_BLOCK

## WORKFLOW
For each candidate (up to 3):
1. Navigate to the candidate URL via mcp__twitter-agent__browser_navigate (read-only, to understand context)
2. Read the full thread to understand context
3. Draft a reply using the best engagement style. Keep it 1-2 sentences. NEVER use em dashes.
4. Post via the CDP script (NOT the MCP browser tools for posting):
     python3 $REPO_DIR/scripts/twitter_browser.py reply \"CANDIDATE_URL\" \"YOUR_REPLY_TEXT\"
   It returns JSON like {\"ok\": true, \"tweet_url\": \"parent\", \"reply_url\": \"https://x.com/m13v_/status/...\"}.
   Parse reply_url from the JSON. If reply_url is missing, empty, or does not contain x.com/m13v_/status/, treat the post as FAILED: do NOT log a row, and mark the candidate as 'failed' instead of 'posted'. NEVER fall back to using the parent/candidate URL as our_url.
5. Self-reply with project link. Immediately reply to YOUR OWN reply_url with a short follow-up AND the matched project's URL.
     python3 $REPO_DIR/scripts/twitter_browser.py self-reply \"YOUR_REPLY_URL\" \"FOLLOW_UP_TEXT\" \"PROJECT_URL\"
   The self-reply subcommand takes the project URL as a SEPARATE third argument and appends it automatically if missing, so you cannot forget the link.
   FOLLOW_UP_TEXT should be 1 short casual sentence, lowercase, no hard sell, no em dashes. Match the parent tweet's language.
   PROJECT_URL must be the exact URL from the project's config (e.g. https://fazm.ai, https://mk0r.com, https://assrt.ai). Look it up from the ALL PROJECTS config above.
   Examples:
     python3 $REPO_DIR/scripts/twitter_browser.py self-reply \"https://x.com/m13v_/status/123\" \"we built something similar for macOS automation\" \"https://fazm.ai\"
     python3 $REPO_DIR/scripts/twitter_browser.py self-reply \"https://x.com/m13v_/status/456\" \"ちょうど同じ理由で作ってる\" \"https://fazm.ai\"
   If the matched project has no URL in the config, skip this step entirely.
6. Log to database. our_url MUST be the reply_url from step 4 (the main reply, not the self-reply). Include: project_name from the candidate's matched_project (or your best guess from the config), engagement_style, language (ISO 639-1 code, e.g. 'en', 'ja', 'zh', 'es'), feedback_report_used=TRUE.
7. After logging to DB, get the post ID from the INSERT (RETURNING id). Then mark the candidate with it:
     UPDATE twitter_candidates SET status='posted', posted_at=NOW(), post_id=THE_POST_ID WHERE id=CANDIDATE_ID

If a thread is no longer available or not relevant, mark it skipped:
UPDATE twitter_candidates SET status='skipped' WHERE id=CANDIDATE_ID

CRITICAL: Reply in the SAME LANGUAGE as the parent tweet. If the tweet is in Japanese, reply in Japanese. If Chinese, reply in Chinese. If English, reply in English. Match the language exactly.
CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: Use twitter_browser.py for posting. Use mcp__twitter-agent__* ONLY for reading threads.
CRITICAL: our_url must always be our own reply permalink (x.com/m13v_/status/...). Logging the parent URL as our_url causes the daily stats report to attribute parent-tweet engagement to us (this happened on 2026-04-14 with a viral @levie thread). Do not repeat it.
CRITICAL: Post at most 3 replies this run. Quality over quantity.
CRITICAL: If a browser tool call is blocked or times out, wait 30 seconds and retry (up to 3 times)." 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"
find "$LOG_DIR" -name "run-twitter-*.log" -mtime +7 -delete 2>/dev/null || true
