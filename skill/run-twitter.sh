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

RUN_START=$(date +%s)
echo "=== Twitter Post Run: $(date) ===" | tee "$LOG_FILE"

# Pick project based on weight distribution
PROJECT=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform twitter 2>/dev/null || echo "Fazm")
PROJECT_JSON=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform twitter --json 2>/dev/null || echo "{}")
echo "Selected project: $PROJECT" | tee -a "$LOG_FILE"

# Generate top performers feedback report (Twitter + project-specific)
TOP_REPORT=$(python3 "$REPO_DIR/scripts/top_performers.py" --platform twitter --project "$PROJECT" 2>/dev/null || echo "(top performers report unavailable)")

# Fetch llms.txt for the selected project (product context for diverse replies)
LLMS_TXT_SRC=$(python3 -c "
import json, os
c = json.load(open('$REPO_DIR/config.json'))
p = next((p for p in c.get('projects',[]) if p['name']=='$PROJECT'), {})
src = p.get('llms_txt', '')
if src:
    print(os.path.expanduser(src))
" 2>/dev/null || echo "")
PRODUCT_CONTEXT=""
if [ -n "$LLMS_TXT_SRC" ]; then
    if [[ "$LLMS_TXT_SRC" == http* ]]; then
        PRODUCT_CONTEXT=$(curl -sfL --max-time 10 "$LLMS_TXT_SRC" | head -150 || echo "")
    elif [ -f "$LLMS_TXT_SRC" ]; then
        PRODUCT_CONTEXT=$(head -150 "$LLMS_TXT_SRC")
    fi
fi
if [ -z "$PRODUCT_CONTEXT" ]; then
    PRODUCT_CONTEXT="(no llms.txt available, use config.json description for product context)"
fi
echo "Product context: $(echo "$PRODUCT_CONTEXT" | wc -c | tr -d ' ') bytes" | tee -a "$LOG_FILE"

# Step 1: Find candidate tweets via API (no browser needed)
CANDIDATES=$(python3 "$REPO_DIR/scripts/find_tweets.py" --project "$PROJECT" --max 20 --json-output 2>/dev/null || echo "[]")
echo "Candidates found: $(echo "$CANDIDATES" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0)" | tee -a "$LOG_FILE"

# Fetch our last 20 replies so Claude can avoid repeating the same themes/takes
RECENT_REPLIES=$(psql "$DATABASE_URL" -t -A -c "
    SELECT LEFT(our_content, 150)
    FROM posts WHERE platform='twitter' AND status='active'
    ORDER BY posted_at DESC LIMIT 20;" 2>/dev/null | paste -sd '|||' - || echo "(unavailable)")

claude --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/twitter-agent-mcp.json" -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for account handle.

## TOPIC AREA FOR THIS RUN: $PROJECT
Your expertise area this run is '$PROJECT'. This means you understand this domain deeply and can
contribute genuinely to conversations about these topics. You are NOT promoting or advertising this project.
You are a practitioner sharing real experience and adding value to discussions.

Use this project's content_angle/voice if it has one, otherwise use the global content_angle.
The project_name for database logging MUST be '$PROJECT'.

## YOUR ROLE
You are someone who works in this space and has opinions, experiences, and knowledge to share.
Your comments should be indistinguishable from any other knowledgeable person in the conversation.
NEVER mention product names, links, websites, or GitHub repos in your comments. Product mentions
happen ONLY in the reply engagement pipeline when people respond to your comments, never in
initial comments. Even if someone asks for a tool, keep the initial comment clean and let
the reply pipeline handle recommendations.

## PRODUCT CONTEXT (from llms.txt, use this to understand the product deeply):
$PRODUCT_CONTEXT

Use this context to find DIVERSE angles when replying. The product has many features and use cases.
Do not fixate on one aspect (e.g. only talking about testing). Vary your angles across replies.

## FEEDBACK FROM PAST PERFORMANCE (use this to write better replies):
$TOP_REPORT

## YOUR RECENT REPLIES (DO NOT repeat these themes, takes, or sentence patterns):
$RECENT_REPLIES

IMPORTANT: Read the recent replies above carefully. If you have already made a point about a topic
(e.g. 'tests are the new code review', 'the test suite IS the spec'), do NOT make the same point
again even if the thread is about a similar topic. Find a DIFFERENT angle, share a different
experience, or skip the thread entirely if you have nothing fresh to add.

## CANDIDATE TWEETS (found via API search, already deduped against DB):
$CANDIDATES

Each candidate may include an 'existing_replies' field showing what others already said in that
thread. Read these before drafting your reply. Do NOT repeat points that other commenters already
made. Find an angle that adds something new to the conversation.

If the API candidates above are weak (off-topic, non-English, low engagement), you may also
browse Twitter search URLs via mcp__twitter-agent__browser_navigate to find better tweets.
IMPORTANT: Whether from API or browser, ONLY reply to tweets with 10+ likes. Low-engagement tweets
(under 10 likes) get no reach and waste our replies. Skip them even if the topic is perfect.

Run the **Workflow: Post** section for **Twitter/X ONLY**. Follow every step:
1. From the candidates above, pick the best tweets where you have genuine expertise to contribute.
   Skip any that are not a good fit (too promotional, off-topic, under 10 likes, etc.).
   If no good candidates, search via browser: mcp__twitter-agent__browser_navigate to a search URL.
2. DEDUP CHECK (MANDATORY before every post): Before replying to any tweet, check if we already posted on it:
   \`\`\`bash
   source ~/social-autoposter/.env
   psql \"\$DATABASE_URL\" -t -A -c \"SELECT id, LEFT(our_content, 80) FROM posts WHERE thread_url LIKE '%TWEET_STATUS_ID%' LIMIT 1;\"
   \`\`\`
   Replace TWEET_STATUS_ID with the tweet's numeric status ID (from the URL).
   If any row is returned, SKIP that tweet and pick another one. Log: \"Skipped tweet TWEET_URL (already posted, post_id=ID)\".
3. Draft the reply as a genuine contribution to the conversation (follow Content Rules, NEVER use em dashes). Match the length to what fits organically: sometimes one punchy sentence is perfect, sometimes 2-3 sentences with a specific anecdote or detail will perform better. Top-performing replies tend to include concrete personal experience (numbers, specific situations, real outcomes). Do NOT pitch, recommend tools, or drop links.
4. Post it using the Python CDP script (no browser MCP needed):
   python3 scripts/twitter_browser.py reply 'TWEET_URL' 'YOUR_REPLY_TEXT'
   Returns JSON with {ok: true, tweet_url, reply_url, verified} on success.
5. Log to database with project_name='$PROJECT' (MUST include feedback_report_used=TRUE in the INSERT).
   CRITICAL: Use reply_url (our reply's URL, e.g. x.com/m13v_/status/...) as our_url in the INSERT.
   Use tweet_url (the parent tweet URL) as thread_url. Do NOT use tweet_url as our_url.
   If reply_url is null, set our_url to NULL in the INSERT (do NOT fall back to tweet_url).

Up to 50 posts per run. If nothing fits, say '## No good tweet found' and stop.

CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: Use ONLY mcp__twitter-agent__* tools for browser actions. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__*.
CRITICAL: If a browser tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). If still blocked, skip and move on." 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"

# Log run to persistent monitor
RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
POSTED=$(grep -c "INSERT INTO posts" "$LOG_FILE" 2>/dev/null) || true
SKIPPED=$(grep -ci "skipped" "$LOG_FILE" 2>/dev/null) || true
FAILED=$(grep -ci "error\|failed\|FAILED" "$LOG_FILE" 2>/dev/null) || true
python3 "$REPO_DIR/scripts/log_run.py" --script "post_twitter" --posted "$POSTED" --skipped "$SKIPPED" --failed "$FAILED" --cost 0 --elapsed "$RUN_ELAPSED"

find "$LOG_DIR" -name "run-twitter-*.log" -mtime +7 -delete 2>/dev/null || true
