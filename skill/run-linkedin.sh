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

echo "=== LinkedIn Post Run: $(date) ===" | tee "$LOG_FILE"

# Pick project based on weight distribution
PROJECT=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform linkedin 2>/dev/null || echo "Fazm")
PROJECT_JSON=$(python3 "$REPO_DIR/scripts/pick_project.py" --platform linkedin --json 2>/dev/null || echo "{}")
echo "Selected project: $PROJECT" | tee -a "$LOG_FILE"

# Generate top performers feedback report (LinkedIn-specific)
TOP_REPORT=$(python3 "$REPO_DIR/scripts/top_performers.py" --platform linkedin 2>/dev/null || echo "(top performers report unavailable)")

claude -p "You are the Social Autoposter.

Read $SKILL_FILE for the full workflow, content rules, and platform details.
Read $REPO_DIR/config.json for account name.

## TARGET PROJECT FOR THIS RUN: $PROJECT
You MUST find LinkedIn posts relevant to this project and comment about it.
Project config: $PROJECT_JSON
Use this project's content_angle/voice if it has one, otherwise use the global content_angle.
The project_name for all posts this run MUST be '$PROJECT'.

## FEEDBACK FROM PAST PERFORMANCE (use this to write better comments):
$TOP_REPORT

Run the **Workflow: Post** section for **LinkedIn ONLY**. Follow every step:
1. Find candidate posts: python3 $REPO_DIR/scripts/find_threads.py --include-linkedin --project '$PROJECT'
   From the output, pick ONLY linkedin candidates (discovery_method: search_url).
   Browse the search URL via mcp__linkedin-agent__browser_navigate to find actual posts.
2. DEDUP CHECK (MANDATORY before every post): Before commenting on any LinkedIn post, check if we already posted on it.
   Extract the activity ID from the post URL (the numeric ID in \`urn:li:activity:ACTIVITY_ID\` or from the feed/update URL).
   \`\`\`bash
   source ~/social-autoposter/.env
   psql \"\$DATABASE_URL\" -t -A -c \"SELECT id, LEFT(our_content, 80) FROM posts WHERE platform='linkedin' AND (thread_url LIKE '%ACTIVITY_ID%' OR our_url LIKE '%ACTIVITY_ID%') LIMIT 1;\"
   \`\`\`
   Replace ACTIVITY_ID with the post's numeric activity ID.
   If any row is returned, SKIP that post and pick another one. Log: \"Skipped LinkedIn post ACTIVITY_ID (already posted, post_id=ID)\".
3. Pick the best LinkedIn post relevant to $PROJECT to comment on
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
4. Draft the comment using the project's voice/angle (follow Content Rules - NEVER use em dashes, professional but casual tone)
5. **POST VIA API** (NOT browser) — Use the LinkedIn API to post the comment:
   \`\`\`bash
   python3 $REPO_DIR/scripts/linkedin_api.py comment ACTIVITY_ID "YOUR COMMENT TEXT"
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
find "$LOG_DIR" -name "run-linkedin-*.log" -mtime +7 -delete 2>/dev/null || true
