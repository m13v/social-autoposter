#!/bin/bash
# Social Autoposter - LinkedIn posting only
# Finds LinkedIn posts and adds up to 30 comments per run.
# Called by launchd every 3 hours.

set -euo pipefail

# Platform lock: wait up to 60min for previous run to finish, then skip
source "$(dirname "$0")/lock.sh"
acquire_lock "linkedin" 3600

[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/run-linkedin-$(date +%Y-%m-%d_%H%M%S).log"

RUN_START=$(date +%s)
echo "=== LinkedIn Post Run: $(date) ===" | tee "$LOG_FILE"

# Auth health check: verify LinkedIn session is valid, re-auth if needed
echo "Checking LinkedIn auth..." | tee -a "$LOG_FILE"
AUTH_EXIT=0
python3 "$REPO_DIR/scripts/linkedin_auth_check.py" 2>&1 | tee -a "$LOG_FILE" || AUTH_EXIT=$?
if [ "$AUTH_EXIT" -eq 1 ]; then
    echo "ERROR: LinkedIn auth check failed and self-healing could not recover. Skipping run." | tee -a "$LOG_FILE"
    python3 "$REPO_DIR/scripts/log_run.py" --script "post_linkedin" --posted 0 --skipped 0 --failed 1 --cost 0 --elapsed $(( $(date +%s) - RUN_START ))
    exit 1
elif [ "$AUTH_EXIT" -eq 2 ]; then
    echo "LinkedIn session was stale, successfully re-authenticated." | tee -a "$LOG_FILE"
fi

# Pick project based on weight distribution
PROJECT=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform linkedin 2>/dev/null || echo "Fazm")
PROJECT_JSON=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform linkedin --json 2>/dev/null || echo "{}")
echo "Selected project: $PROJECT" | tee -a "$LOG_FILE"

# Generate top performers feedback report (LinkedIn-specific)
TOP_REPORT=$(python3 "$REPO_DIR/scripts/top_performers.py" --platform linkedin 2>/dev/null || echo "(top performers report unavailable)")

claude --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/linkedin-agent-mcp.json" -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for account name.

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

## FEEDBACK FROM PAST PERFORMANCE (use this to write better comments):
$TOP_REPORT

Run the **Workflow: Post** section for **LinkedIn ONLY**. Follow every step:
1. Find candidate posts: python3 -c \"import json,urllib.parse; c=json.load(open('$REPO_DIR/config.json')); p=next((x for x in c.get('projects',[]) if x['name'].lower()=='$PROJECT'.lower()),{}); topics=p.get('linkedin_topics',c.get('linkedin_topics',[])); print(json.dumps([{'platform':'linkedin','url':'https://www.linkedin.com/search/results/content/?keywords='+urllib.parse.quote(t)+'&sortBy=%22date_posted%22','title':'Search: '+t,'discovery_method':'search_url','search_topic':t} for t in topics],indent=2))\"
   From the output, pick ONLY linkedin candidates (discovery_method: search_url).
   For each search URL, run: python3 $REPO_DIR/scripts/linkedin_browser.py search 'SEARCH_URL'
   This returns {activity_ids: [...], posts: [...]}. Use activity IDs for dedup and commenting.
   If linkedin_browser.py fails, fall back to browsing via mcp__linkedin-agent__browser_navigate.
2. DEDUP CHECK (MANDATORY before every post): Before commenting on any LinkedIn post, check if we already posted on it.
   Extract the activity ID from the post URL (the numeric ID in \`urn:li:activity:ACTIVITY_ID\` or from the feed/update URL).
   \`\`\`bash
   source ~/social-autoposter/.env
   psql \"\$DATABASE_URL\" -t -A -c \"SELECT id, LEFT(our_content, 80) FROM posts WHERE platform='linkedin' AND (thread_url LIKE '%ACTIVITY_ID%' OR our_url LIKE '%ACTIVITY_ID%') LIMIT 1;\"
   \`\`\`
   Replace ACTIVITY_ID with the post's numeric activity ID.
   If any row is returned, SKIP that post and pick another one. Log: \"Skipped LinkedIn post ACTIVITY_ID (already posted, post_id=ID)\".
3. Pick the best LinkedIn post where you have genuine expertise to contribute.
3. **EXTRACT THE ACTIVITY ID** — Before commenting, extract the numeric activity ID from the post.
   Run this JS via mcp__linkedin-agent__browser_run_code to get the activity ID:
   \`\`\`javascript
   async (page) => {
     const url = page.url();
     let match = url.match(/activity[:%3A](\\d+)/i);
     if (match) return match[1];
     const postEl = document.querySelector('[data-urn*=\"activity\"], [data-id*=\"activity\"]');
     if (postEl) {
       const urn = postEl.getAttribute('data-urn') || postEl.getAttribute('data-id');
       match = urn.match(/activity:(\\d+)/);
       if (match) return match[1];
     }
     return null;
   }
   \`\`\`
4. Draft the comment as a genuine contribution to the conversation (follow Content Rules, NEVER use em dashes, professional but casual tone). Share experience, ask questions, add nuance. Do NOT pitch, recommend tools, or drop links. Product mentions happen ONLY in the reply engagement pipeline, never in initial comments.
5. **POST VIA API** (NOT browser) — Use the LinkedIn API to post the comment:
   \`\`\`bash
   python3 $REPO_DIR/scripts/linkedin_api.py comment ACTIVITY_ID 'YOUR COMMENT TEXT'
   \`\`\`
   This returns JSON with {ok, comment_urn, our_url, activity_id}. Use our_url for the database INSERT.
   If the API call fails, fall back to browser posting as before.
6. Log to database with project_name='$PROJECT' (MUST include feedback_report_used=TRUE in the INSERT). Use the our_url from the API response.

Up to 30 posts per run. If nothing fits, say '## No good post found' and stop.

CRITICAL: Ignore the 'Max 40 posts per 24 hours' limit in SKILL.md. The actual daily limit is 4000 posts. Post up to 30 per this run.
CRITICAL: NEVER use em dashes in any content. Use commas, periods, or regular dashes (-) instead.
CRITICAL: Use ONLY mcp__linkedin-agent__* tools. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__*.
CRITICAL: If a browser tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). If still blocked, skip and stop." 2>&1 | tee -a "$LOG_FILE"

echo "=== Run complete: $(date) ===" | tee -a "$LOG_FILE"

# Log run to persistent monitor
RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
POSTED=$(grep -c "INSERT INTO posts" "$LOG_FILE" 2>/dev/null) || true
SKIPPED=$(grep -c -i "skipped" "$LOG_FILE" 2>/dev/null) || true
FAILED=$(grep -c -iE "error|failed|FAILED" "$LOG_FILE" 2>/dev/null) || true
python3 "$REPO_DIR/scripts/log_run.py" --script "post_linkedin" --posted "$POSTED" --skipped "$SKIPPED" --failed "$FAILED" --cost 0 --elapsed "$RUN_ELAPSED"

find "$LOG_DIR" -name "run-linkedin-*.log" -mtime +7 -delete 2>/dev/null || true
